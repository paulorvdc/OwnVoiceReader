"""ingest/ocr.py — M3: OCR fallback for pages/regions without a text layer.

This is the OCR side of the "interchangeable contract" (BUILD_PLAN.md §4): a
scanned (image-only) PDF must read the same as a typed one, with no downstream
branching on origin. The text layer already emits :class:`Line` objects
(:mod:`ownvoicereader.ingest.text_extractor`); OCR emits the **same**
:class:`Line` shape, tagged ``source = BlockSource.OCR``, so both feed
:mod:`reading_order` — and the whole reader — identically.

Pipeline
--------
  1. :func:`text_extractor.flag_sparse_pages` already marks the pages whose text
     layer is missing or trivial (< 30 chars) — the OCR candidates.
  2. For each sparse page we rasterize it (PyMuPDF ``get_pixmap`` at
     :data:`DEFAULT_OCR_DPI`) and run an :class:`OcrEngine`, which returns
     line-level :class:`OcrLine` results in *image-pixel* coordinates.
  3. We map each pixel box back to PDF user space and emit a :class:`Line`
     (``source=OCR``) per text line. Those lines are merged with the text-layer
     lines and read together.

Design mirrors :mod:`ownvoicereader.voice.tts_engine` and
:mod:`ownvoicereader.llm.client`:

  * :class:`OcrEngine`     — the abstract interface the pipeline depends on.
  * :class:`RapidOcrEngine`— the default (A4): the ``rapidocr`` library, a
    fully-local ONNX detection+recognition pipeline (its models ship with the
    package, so there is no model download).
  * :class:`TesseractEngine`— the alt (A4): ``pytesseract`` over the system
    ``tesseract`` binary (word-level, grouped into lines).
  * :class:`NullOcrEngine` — the graceful "OCR is off" state: reports
    unavailable and yields nothing, so the reader degrades to the text layer
    instead of crashing (the M6 "feature-off" path).
  * :func:`build_ocr_engine` — the factory: the best available engine in the A4
    order (RapidOCR, then Tesseract), or a :class:`NullOcrEngine` if neither is
    installed.

Everything heavy is imported *lazily*: ``rapidocr`` / ``pytesseract`` / ``numpy``
live in the ``[ocr]`` extra, so a bare core install imports this module without
them present. The OCR model itself is loaded only on the first page it reads
(cached in the engine instance), never at import or construction.
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .text_extractor import Line
from ..model import BlockSource, ReadingDocument

log = logging.getLogger("ownvoicereader.ingest.ocr")

# Rasterize sparse pages at this resolution for OCR. 300 dpi is the usual OCR
# sweet spot (legible for most fonts, modest memory); overridable via the
# OWNVOICEREADER_OCR_DPI environment variable.
DEFAULT_OCR_DPI = 300
# A text line OCR'd with confidence below this is dropped as likely noise (a
# smudge, a page-number artifact, a half-detected glyph).
DEFAULT_MIN_CONFIDENCE = 0.5
# A4 engine order: RapidOCR default, Tesseract the alt.
_ENGINE_ORDER: tuple[str, ...] = ("rapidocr", "tesseract")


# --------------------------------------------------------------------------- #
# The engine-agnostic result shape
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OcrLine:
    """One OCR'd text line, in *image-pixel* coordinates.

    Each engine normalizes its raw output to this, and :func:`ocr_page` maps it
    into the unified :class:`Line` shape (PDF coordinates + ``source=OCR``).
    ``confidence`` is OCR-only metadata: it is used to filter noise but is *not*
    carried onto :class:`Line`, so the text/OCR contract stays byte-for-byte the
    same (a :class:`Line` has no confidence field for either origin).
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0


# --------------------------------------------------------------------------- #
# Engine interface
# --------------------------------------------------------------------------- #
class OcrEngine(ABC):
    """A local OCR engine producing line-level :class:`OcrLine` results.

    An engine is given a rendered page image (a ``numpy`` H×W×3 array, the common
    denominator both backends consume) and returns the page's text lines in the
    image's *pixel* coordinates. :func:`ocr_page` owns the pixel→PDF mapping so
    the engine stays decoupled from PyMuPDF.
    """

    name: str = "abstract"

    @property
    @abstractmethod
    def available(self) -> bool:
        """True if this engine can run right now (library — and model — present)."""

    @abstractmethod
    def ocr(self, image: "object") -> list[OcrLine]:
        """OCR one page image → line-level results in image-pixel coordinates."""


# --------------------------------------------------------------------------- #
# RapidOCR (default, A4)
# --------------------------------------------------------------------------- #
class RapidOcrEngine(OcrEngine):
    """The default engine: the ``rapidocr`` library (A4).

    A fully-local ONNX detection+recognition pipeline (PP-OCRv6); its models
    ship with the package, so there is no model download on first use. The heavy
    import and the one-time model load happen lazily, on the first page read.
    """

    name = "rapidocr"

    def __init__(self, dpi: int = DEFAULT_OCR_DPI):
        self.dpi = dpi
        self._engine = None
        self._import_ok: Optional[bool] = None

    @property
    def available(self) -> bool:
        """True if the ``rapidocr`` library can be imported.

        The model *ships with the package* (no download), so "the library
        imports" is the availability signal — it is **not** loaded here. The
        one-time model load happens lazily in :meth:`ocr` (the first page read),
        cached in the instance, so a typed-only PDF pays nothing: an engine is
        built (cheaply) but its model is never loaded unless a sparse page
        actually needs it.
        """
        if self._import_ok is None:
            try:
                import rapidocr  # noqa: F401 - the [ocr] extra
                self._import_ok = True
            except Exception as exc:  # pragma: no cover - only when [ocr] absent
                log.info("RapidOCR unavailable: %s", exc)
                self._import_ok = False
        return self._import_ok

    def _get(self):
        """Construct and cache the underlying ``rapidocr`` engine (lazy)."""
        if self._engine is None:
            from rapidocr import RapidOCR  # lazy: the [ocr] extra

            self._engine = RapidOCR()
        return self._engine

    def ocr(self, image: "object") -> list[OcrLine]:
        """Run detection+recognition; normalize the parallel result lists to
        :class:`OcrLine` (one per detected text line)."""
        result = self._get()(image)
        out: list[OcrLine] = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            x0, y0, x1, y1 = _corners_to_bbox(box)
            out.append(OcrLine(text, x0, y0, x1, y1, float(score)))
        return out


# --------------------------------------------------------------------------- #
# Tesseract (alt, A4)
# --------------------------------------------------------------------------- #
def _tesseract_binary() -> Optional[str]:
    """Locate the system ``tesseract`` binary (env override, then PATH)."""
    env_bin = os.environ.get("OWNVOICEREADER_TESSERACT_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin
    found = shutil.which("tesseract")
    return found


class TesseractEngine(OcrEngine):
    """The alt engine: ``pytesseract`` over the system ``tesseract`` binary (A4).

    ``image_to_data`` returns *word-level* boxes; we group words into text lines
    by ``line_num`` (PSM 6 — "assume a single uniform block of text", which gives
    good line granularity for the single-column pages OCR most often meets) and
    take the union box + the minimum word confidence per line.
    """

    name = "tesseract"

    def __init__(self, lang: str = "eng", psm: int = 6, dpi: int = DEFAULT_OCR_DPI):
        self.lang = lang
        self.psm = psm
        self.dpi = dpi

    @property
    def available(self) -> bool:
        """True if both the ``pytesseract`` wrapper and the tesseract binary
        exist (the binary is a system dependency, not a pip one)."""
        try:
            import pytesseract  # noqa: F401
        except Exception:
            return False
        return _tesseract_binary() is not None

    def ocr(self, image: "object") -> list[OcrLine]:
        import pytesseract
        from collections import defaultdict

        data = pytesseract.image_to_data(
            image, lang=self.lang, config=f"--psm {self.psm}",
            output_type=pytesseract.Output.DICT,
        )
        # Group words into lines by tesseract's line_num.
        groups: dict[int, list] = defaultdict(list)
        for i in range(len(data["text"])):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            groups[data["line_num"][i]].append(
                (data["left"][i], data["top"][i], data["width"][i],
                 data["height"][i], word, int(data["conf"][i] or 0))
            )
        out: list[OcrLine] = []
        for items in groups.values():
            x0 = min(w[0] for w in items)
            y0 = min(w[1] for w in items)
            x1 = max(w[0] + w[2] for w in items)
            y1 = max(w[1] + w[3] for w in items)
            # Read left→right within the line; confidence is the weakest word.
            text = " ".join(w[4] for w in sorted(items, key=lambda w: w[0]))
            confidence = min((w[5] for w in items), default=0) / 100.0
            out.append(OcrLine(text, x0, y0, x1, y1, confidence))
        return out


# --------------------------------------------------------------------------- #
# Null engine (graceful "off")
# --------------------------------------------------------------------------- #
class NullOcrEngine(OcrEngine):
    """OCR is not installed: report unavailable and yield nothing, so the reader
    degrades to the text layer instead of crashing (the M6 "feature-off" path,
    the OCR analogue of :class:`NullLlmClient`)."""

    name = "null"

    @property
    def available(self) -> bool:
        return False

    def ocr(self, image: "object") -> list[OcrLine]:
        return []


# --------------------------------------------------------------------------- #
# Geometry: image pixels -> PDF user space
# --------------------------------------------------------------------------- #
def _corners_to_bbox(box: "object") -> tuple[float, float, float, float]:
    """Collapse an OCR box (a 4-corner list/array ``[[x,y], …]``) to an
    axis-aligned pixel bbox ``[x0, y0, x1, y1]``.

    ``rapidocr`` returns a 4×2 array of corners; some backends return an
    axis-aligned ``[x0, y0, x1, y1]`` directly. Both are handled.
    """
    import numpy as np

    arr = np.asarray(box, dtype=float)
    if arr.ndim == 2 and arr.shape[1] == 2:
        xs = arr[:, 0]
        ys = arr[:, 1]
    elif arr.ndim == 1 and arr.size == 4:
        xs = arr[0::2]
        ys = arr[1::2]
    else:  # pragma: no cover - defensive
        flat = arr.ravel()
        xs = flat[0::2]
        ys = flat[1::2]
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def _render_page(page, dpi: int):
    """Rasterize a PyMuPDF page to an RGB ``numpy`` array at ``dpi``.

    PyMuPDF's pixmap carries raw samples (RGB or RGBA or 1-channel); we normalize
    to 3-channel RGB so every backend sees the same shape.
    """
    import numpy as np

    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    if pix.n == 4:
        arr = arr[:, :, :3]            # drop the alpha channel
    elif pix.n == 1:
        arr = np.repeat(arr, 3, axis=2)  # grayscale -> RGB
    return arr


# --------------------------------------------------------------------------- #
# Page + sparse-page OCR
# --------------------------------------------------------------------------- #
def ocr_page(page, page_no: int, engine: OcrEngine, dpi: int = DEFAULT_OCR_DPI,
             min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> list[Line]:
    """OCR one page → a list of unified :class:`Line` (PDF coords, source=OCR).

    Rasterizes the page at ``dpi``, runs ``engine``, maps each result's pixel box
    back to PDF user space (``pdf_x = rect.x0 + px / scale``), and emits one
    :class:`Line` per detected text line so it flows through
    :mod:`reading_order` exactly like a text-layer line. Lines below
    ``min_confidence`` are dropped as noise.
    """
    arr = _render_page(page, dpi)
    rect = page.rect
    # The pixmap covers the page's rect; scale maps pixel -> points. A 0-width or
    # 0-height page (a blank/odd page) cannot be mapped, so it yields no lines.
    scale_x = arr.shape[1] / rect.width if rect.width else 0.0
    scale_y = arr.shape[0] / rect.height if rect.height else 0.0
    if scale_x == 0 or scale_y == 0:
        return []

    lines: list[Line] = []
    for r in engine.ocr(arr):
        text = " ".join(r.text.split())
        if not text or r.confidence < min_confidence:
            continue
        x0 = rect.x0 + r.x0 / scale_x
        y0 = rect.y0 + r.y0 / scale_y
        x1 = rect.x0 + r.x1 / scale_x
        y1 = rect.y0 + r.y1 / scale_y
        lines.append(
            Line(page=page_no, text=text, x0=x0, y0=y0, x1=x1, y1=y1,
                 source=BlockSource.OCR)
        )
    return lines


def ocr_sparse_pages(doc: ReadingDocument, sparse_pages: list[int],
                     engine: OcrEngine, source=None,
                     dpi: int = DEFAULT_OCR_DPI,
                     min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> list[Line]:
    """OCR every page in ``sparse_pages`` → a merged :class:`Line` list.

    The returned lines are in page order (each page's own lines in order); the
    caller concatenates them with the text-layer lines and runs
    :mod:`reading_order` over the combined set, so a scanned and a typed page are
    read together in one pass. A failure on *one* page is logged and skipped so
    the rest are still read (a bad scan never sinks the document).
    """
    owns = source is None
    if owns:
        from .pdf_source import PdfSource

        source = PdfSource.from_path(doc.source_path)
    try:
        out: list[Line] = []
        for pno in sparse_pages:
            try:
                out.extend(
                    ocr_page(source.doc[pno], pno, engine, dpi=dpi,
                             min_confidence=min_confidence)
                )
            except Exception:  # pragma: no cover - a bad page must not abort OCR
                log.warning("OCR failed on page %d; skipping it", pno, exc_info=True)
                continue
        return out
    finally:
        if owns:
            source.close()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def _env_dpi() -> int:
    try:
        return int(os.environ.get("OWNVOICEREADER_OCR_DPI", DEFAULT_OCR_DPI))
    except ValueError:
        return DEFAULT_OCR_DPI


def _make_engine(name: str) -> Optional[OcrEngine]:
    """Build the named engine, or ``None`` if it is not a known name."""
    dpi = _env_dpi()
    name = (name or "").lower()
    if name == "rapidocr":
        return RapidOcrEngine(dpi=dpi)
    if name == "tesseract":
        return TesseractEngine(dpi=dpi)
    return None


def build_ocr_engine(prefer: Optional[str] = None) -> OcrEngine:
    """Return the best available local OCR engine.

    Preference order (A4): **RapidOCR** (default), then **Tesseract**. An
    explicit ``prefer`` (or the ``OWNVOICEREADER_OCR_ENGINE`` env var) is tried
    first. Construction is cheap — the heavy model loads lazily on the first page
    read — so this is safe to call on every document load. If no engine is
    available a :class:`NullOcrEngine` is returned, giving callers one code path
    while the reader simply degrades to the text layer (never a crash).
    """
    order = list(_ENGINE_ORDER)
    want = prefer or os.environ.get("OWNVOICEREADER_OCR_ENGINE")
    if want:
        want = want.lower()
        # The requested engine is tried *first*, ahead of the A4 default order;
        # the remaining engines keep their relative order (de-duplicated).
        order = [want] + [n for n in order if n != want]
    for name in order:
        engine = _make_engine(name)
        if engine is not None and engine.available:
            log.info("OCR: using %s engine", name)
            return engine
    log.warning(
        "OCR: no engine available — sparse (scanned) pages will not be read. "
        "Install the [ocr] extra:  pip install 'ownvoicereader[ocr]'"
    )
    return NullOcrEngine()
