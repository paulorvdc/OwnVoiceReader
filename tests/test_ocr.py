"""Tests for M3 OCR (hermetic core + one conditional parity test).

The "interchangeable contract" (BUILD_PLAN.md §4): a scanned page must read the
same as a typed one, with no downstream branching on origin. These tests pin
that contract:

  * The OCR path emits the **same** :class:`Line`/``Block`` shape as the text
    layer (tagged ``source = BlockSource.OCR``), and those lines flow through
    ``reading_order`` exactly like text lines.
  * ``build_document`` reads *sparse* pages via OCR and merges them with the text
    layer, and degrades gracefully (no crash, text still read) when no engine is
    installed.

The heavy OCR backends live in the ``[ocr]`` extra, so most tests run hermetically
against a small :class:`_StubEngine` + a generated image-only PDF. One
integration test (``test_parity_scanned_reads_like_text``) runs the *real* engine
on the CEUR text/scanned pair, and skips itself when the engine or the sample is
absent (so a bare ``[test]`` install never needs the heavy OCR models).
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from ownvoicereader.ingest import (
    build_ocr_engine,
    build_document,
    ocr_sparse_pages,
    open_pdf,
)
from ownvoicereader.ingest.ocr import (
    OcrEngine,
    OcrLine,
    NullOcrEngine,
    _corners_to_bbox,
)
from ownvoicereader.ingest.reading_order import order_lines
from ownvoicereader.ingest.text_extractor import Line
from ownvoicereader.model import BlockKind, BlockSource

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _StubEngine(OcrEngine):
    """A hermetic engine: returns canned :class:`OcrLine`s in pixel coords
    without any heavy backend, so the geometry-mapping + merge path is tested
    deterministically. ``available`` is toggleable for the graceful-degradation
    cases."""

    name = "stub"

    def __init__(self, lines: list[OcrLine], available: bool = True):
        self._lines = lines
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def ocr(self, image: "object") -> list[OcrLine]:
        return self._lines


def _image_only_pdf(path, width=612, height=792, pages=1):
    """An image-only PDF (a raster per page, *no* text layer) so
    ``flag_sparse_pages`` marks every page for OCR. Used to exercise the real
    rasterize→OCR→map path without a scanned sample."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 200, 200))
        pix.clear_with(0)
        page.insert_image(page.rect, pixmap=pix)
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# The "same shape" contract
# --------------------------------------------------------------------------- #
def test_ocr_line_has_same_shape_as_text_line():
    """An OCR-sourced Line carries the exact same fields as a text-sourced one;
    the only difference is ``source`` (and OCR leaves font cues at their
    defaults — a scan has no font metadata)."""
    from ownvoicereader.ingest.text_extractor import Line as _L

    text = _L(page=0, text="hello", x0=1.0, y0=2.0, x1=3.0, y1=4.0,
              font_size=11.0, is_bold=False)
    ocr = _L(page=0, text="hello", x0=1.0, y0=2.0, x1=3.0, y1=4.0,
             source=BlockSource.OCR)
    assert text.source is BlockSource.TEXT
    assert ocr.source is BlockSource.OCR
    # Same field names; the geometric + text fields are identical.
    assert (text.page, text.text, text.x0, text.y0, text.x1, text.y1,
            text.is_bold) == (ocr.page, ocr.text, ocr.x0, ocr.y0, ocr.x1, ocr.y1,
                              ocr.is_bold)
    # A scan has no font metadata, so OCR leaves font_size at its default (0),
    # unlike the text layer's real 11pt. Same shape, honestly-different cue.
    assert ocr.font_size == 0.0 and text.font_size == 11.0


def test_ocr_lines_flow_into_blocks_with_ocr_source():
    """OCR lines feed reading_order and come back as TEXT blocks tagged OCR —
    the reader never branches on origin."""
    lines = [
        Line(page=0, text="alpha first line", x0=50, y0=100, x1=250, y1=111,
             source=BlockSource.OCR),
        Line(page=0, text="alpha second line", x0=50, y0=120, x1=250, y1=131,
             source=BlockSource.OCR),
    ]
    blocks = order_lines(lines, page_geometry=[(612, 792)], page_count=1)
    assert blocks, "expected at least one block"
    for b in blocks:
        assert b.kind is BlockKind.TEXT
        assert b.source is BlockSource.OCR
    assert "alpha first line" in blocks[0].text
    assert "alpha second line" in " ".join(b.text for b in blocks)


# --------------------------------------------------------------------------- #
# Geometry: image pixels -> PDF user space
# --------------------------------------------------------------------------- #
def test_corners_to_bbox_collapses_quad():
    import numpy as np

    # A 4-corner quad (possibly rotated) collapses to its axis-aligned bbox.
    quad = np.array([[0, 0], [10, 0], [10, 5], [0, 5]], dtype=float)
    x0, y0, x1, y1 = _corners_to_bbox(quad)
    assert (x0, y0, x1, y1) == (0.0, 0.0, 10.0, 5.0)
    # A flat [x0, y0, x1, y1] box is also accepted.
    assert _corners_to_bbox([0.0, 0.0, 8.0, 4.0]) == (0.0, 0.0, 8.0, 4.0)


def test_ocr_page_maps_lines_top_to_bottom(tmp_path):
    """ocr_sparse_pages rasterizes a real image-only page and maps the stub
    engine's pixel boxes back to PDF coords: a line drawn higher maps to a
    smaller y, and the resulting lines are all tagged OCR."""
    path = _image_only_pdf(tmp_path / "img.pdf")
    doc = open_pdf(path)
    stub = _StubEngine([
        OcrLine("lower line", 50, 300, 300, 320, 0.99),
        OcrLine("upper line", 50, 50, 300, 70, 0.99),
    ])
    lines = ocr_sparse_pages(doc, [0], stub)
    assert len(lines) == 2
    assert all(l.source is BlockSource.OCR for l in lines)
    # The "upper" line (smaller pixel y) must read before the "lower" one.
    ys = sorted(l.y0 for l in lines)
    assert ys[0] < ys[1]
    upper = min(lines, key=lambda l: l.y0)
    assert upper.text == "upper line"
    # Mapped into PDF user space, y sits within the page height.
    assert 0 <= upper.y0 < 792


# --------------------------------------------------------------------------- #
# build_document wiring: sparse pages -> OCR -> merged blocks
# --------------------------------------------------------------------------- #
def test_build_document_ocr_merges_sparse_pages(tmp_path):
    """build_document runs OCR on a sparse page and merges the OCR'd lines,
    tagging the resulting blocks source=OCR. A stub engine keeps this hermetic."""
    path = _image_only_pdf(tmp_path / "img.pdf")
    doc = open_pdf(path)
    stub = _StubEngine([
        OcrLine("Improving Natural Product Extraction", 50, 100, 400, 120, 1.0),
        OcrLine("Knowledge graphs play a vital role", 50, 140, 400, 160, 1.0),
    ])
    build_document(doc, ocr=stub)
    text_blocks = [b for b in doc.blocks if b.kind is BlockKind.TEXT]
    assert text_blocks, "OCR should have produced text blocks"
    assert all(b.source is BlockSource.OCR for b in text_blocks)
    assert 0 in doc.ocr_pages
    joined = " ".join(b.text for b in text_blocks)
    assert "Improving Natural Product Extraction" in joined


def test_build_document_skips_figure_table_on_scanned_pages(tmp_path):
    """A fully-scanned page's single raster is the whole page (the text we OCR),
    not a figure — so figure/table extraction skips OCR'd pages."""
    path = _image_only_pdf(tmp_path / "img.pdf")
    doc = open_pdf(path)
    stub = _StubEngine([OcrLine("a scanned line of text", 50, 100, 400, 120, 1.0)])
    build_document(doc, ocr=stub)
    non_text = [b for b in doc.blocks if b.kind is not BlockKind.TEXT]
    assert non_text == [], f"scanned page should add no figure/table blocks: {non_text}"


def test_build_document_graceful_without_engine(tmp_path):
    """With the graceful NullOcrEngine, a sparse (text-less) page simply yields
    no text to read — no crash, and the document still builds. (A text-less page
    may still surface its raster as a figure block; that is display-only and
    harmless to the spoken path, which reads text blocks only.)"""
    path = _image_only_pdf(tmp_path / "img.pdf")
    doc = open_pdf(path)
    build_document(doc, ocr=NullOcrEngine())
    text_blocks = [b for b in doc.blocks if b.kind is BlockKind.TEXT]
    assert text_blocks == []  # nothing read from the text-less page
    assert doc.sparse_pages == [0]
    assert doc.ocr_pages == []  # nothing was read via OCR


def test_ocr_enabled_false_skips_ocr(tmp_path):
    """ocr_enabled=False leaves sparse pages unread even when an engine is passed
    (the toggle is honored before the engine is consulted)."""
    path = _image_only_pdf(tmp_path / "img.pdf")
    doc = open_pdf(path)
    stub = _StubEngine([OcrLine("should NOT be read", 50, 100, 400, 120, 1.0)])
    build_document(doc, ocr=stub, ocr_enabled=False)
    text_blocks = [b for b in doc.blocks if b.kind is BlockKind.TEXT]
    assert text_blocks == []
    assert doc.ocr_pages == []


def test_ocr_sparse_pages_skips_a_failing_page(tmp_path):
    """A failure on one sparse page is logged and skipped so the rest are read."""
    path = _image_only_pdf(tmp_path / "img.pdf", pages=2)
    doc = open_pdf(path)

    class FlakyEngine(OcrEngine):
        name = "flaky"

        def __init__(self):
            self.calls = 0

        @property
        def available(self) -> bool:
            return True

        def ocr(self, image) -> list[OcrLine]:
            self.calls += 1
            if self.calls == 1:  # the first page fails; later ones recover
                raise RuntimeError("boom on first page")
            return [OcrLine("recovered on a later page", 50, 100, 400, 120, 1.0)]

    engine = FlakyEngine()
    lines = ocr_sparse_pages(doc, [0, 1], engine)
    # Page 0 failed and was skipped; page 1's line survived.
    assert [l.text for l in lines] == ["recovered on a later page"]


# --------------------------------------------------------------------------- #
# Engine factory / graceful degradation
# --------------------------------------------------------------------------- #
class _Unavailable(OcrEngine):
    """A stand-in engine that reports itself unavailable (the 'extra not
    installed' case, without needing to actually uninstall anything)."""

    def __init__(self, name: str = "unavailable"):
        self.name = name

    @property
    def available(self) -> bool:
        return False

    def ocr(self, image) -> list[OcrLine]:
        return []


class _NamedEngine(OcrEngine):
    """A stand-in engine with a fixed name + availability (for factory tests)."""

    def __init__(self, name: str, available: bool = True):
        self.name = name
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def ocr(self, image) -> list[OcrLine]:
        return []


def test_null_ocr_engine_reports_unavailable():
    eng = NullOcrEngine()
    assert eng.available is False
    assert eng.ocr(object()) == []


def test_build_ocr_engine_returns_a_real_or_null_engine():
    eng = build_ocr_engine()
    assert isinstance(eng, OcrEngine)
    assert eng.name in ("rapidocr", "tesseract", "null")


def test_build_ocr_engine_falls_back_to_null(monkeypatch):
    """When no engine is available, the factory returns a NullOcrEngine (the
    graceful 'feature-off' state) rather than raising."""
    import ownvoicereader.ingest.ocr as ocrmod

    monkeypatch.setattr(ocrmod, "RapidOcrEngine",
                        lambda dpi=300: _Unavailable(name="rapidocr"))
    monkeypatch.setattr(ocrmod, "TesseractEngine",
                        lambda dpi=300, lang="eng", psm=6: _Unavailable(name="tesseract"))
    eng = build_ocr_engine()
    assert isinstance(eng, NullOcrEngine)
    assert eng.available is False


def test_build_ocr_engine_honours_prefer_env(monkeypatch):
    """A requested engine is tried first; when it is unavailable the factory
    falls through to the next available one."""
    import ownvoicereader.ingest.ocr as ocrmod

    monkeypatch.setenv("OWNVOICEREADER_OCR_ENGINE", "tesseract")
    # Tesseract (the requested one) is unavailable; RapidOCR is available.
    monkeypatch.setattr(ocrmod, "TesseractEngine",
                        lambda dpi=300, lang="eng", psm=6: _Unavailable(name="tesseract"))
    monkeypatch.setattr(ocrmod, "RapidOcrEngine",
                        lambda dpi=300: _NamedEngine("rapidocr", available=True))
    eng = build_ocr_engine()
    assert eng.name == "rapidocr"  # tesseract unavailable -> rapidocr
    monkeypatch.delenv("OWNVOICEREADER_OCR_ENGINE", raising=False)


def test_build_ocr_engine_prefers_available_requested(monkeypatch):
    """When the requested engine is *available* it is selected ahead of the A4
    default order — via an explicit ``prefer`` and via the env var."""
    import ownvoicereader.ingest.ocr as ocrmod

    monkeypatch.setattr(ocrmod, "TesseractEngine",
                        lambda dpi=300, lang="eng", psm=6: _NamedEngine("tesseract", available=True))
    monkeypatch.setattr(ocrmod, "RapidOcrEngine",
                        lambda dpi=300: _NamedEngine("rapidocr", available=True))
    # Explicit prefer=...
    assert build_ocr_engine(prefer="tesseract").name == "tesseract"
    # ... and via the OS environment (A6: never persisted).
    monkeypatch.setenv("OWNVOICEREADER_OCR_ENGINE", "tesseract")
    assert build_ocr_engine().name == "tesseract"


# --------------------------------------------------------------------------- #
# Parity: a scanned page reads like a typed one (conditional on the real engine
# and the CEUR sample being present)
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent.parent
_TEXT_PDF = _HERE / "test_pdfs" / "1-column_ceur.pdf"
_SCANNED_PDF = _HERE / "test_pdfs" / "1-column_ceur_scanned.pdf"
# Distinctive words that a good OCR of the CEUR abstract should recover,
# case-insensitively. Chosen to be long + unusual enough to be meaningful.
_PARITY_PROBES = (
    "improving", "natural", "product", "extraction",
    "knowledge", "graph", "abstract", "keyword",
)


def _ocr_engine_or_skip():
    """Return a usable OCR engine, or skip the parity test if the [ocr] extra
    (and a real backend) is not installed."""
    engine = build_ocr_engine()
    if not getattr(engine, "available", False):
        pytest.skip("no OCR engine available (install the [ocr] extra)")
    return engine


@pytest.mark.skipif(
    not (_TEXT_PDF.exists() and _SCANNED_PDF.exists()),
    reason="CEUR text/scanned sample pair not present",
)
def test_parity_scanned_reads_like_text():
    """End-to-end parity: the scanned CEUR PDF, read via OCR, recovers the same
    content as its typed twin. This is the headline M3 invariant."""
    engine = _ocr_engine_or_skip()

    text_doc = open_pdf(str(_TEXT_PDF))
    build_document(text_doc)
    scan_doc = open_pdf(str(_SCANNED_PDF))
    build_document(scan_doc, ocr=engine)

    text_blocks = [b for b in text_doc.blocks if b.kind is BlockKind.TEXT]
    scan_blocks = [b for b in scan_doc.blocks if b.kind is BlockKind.TEXT]

    # 1) The scanned doc has content, and every one of its text blocks is OCR'd.
    assert scan_blocks, "OCR produced no text blocks from the scanned PDF"
    assert all(b.source is BlockSource.OCR for b in scan_blocks)
    assert scan_doc.ocr_pages  # some pages were read via OCR

    # 2) The OCR recovered a comparable *amount* of text to the typed version.
    text_chars = sum(len(b.text) for b in text_blocks)
    scan_chars = sum(len(b.text) for b in scan_blocks)
    assert text_chars > 0
    assert scan_chars >= 0.5 * text_chars, (
        f"OCR recovered only {scan_chars} of {text_chars} chars"
    )

    # 3) The distinctive content words survive OCR (fuzzy, case-insensitive).
    scan_text = " ".join(b.text for b in scan_blocks).lower()
    missing = [w for w in _PARITY_PROBES if w not in scan_text]
    assert not missing, f"OCR'd text is missing probes: {missing}"


if __name__ == "__main__":  # pragma: no cover - manual run
    raise SystemExit(pytest.main([__file__, "-v"]))
