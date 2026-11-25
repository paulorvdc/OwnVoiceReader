"""ingest/pdf_source.py — open a PDF, read page count + metadata (M0).

Higher ingest steps (text blocks, figures/tables, OCR, reading order) build
on top of this in later milestones. For M0 this validates that a file is a
PDF, reads its info dictionary, and counts pages using PyMuPDF.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pymupdf  # PyMuPDF; `fitz` is a deprecated alias, so import the real name.

from ..model import DocumentMetadata, ReadingDocument

log = logging.getLogger(__name__)

# PDF magic number: the first 5 bytes are the literal string "%PDF-".
_PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(path: Path) -> bool:
    """Cheap, dependency-free pre-check before handing the file to PyMuPDF."""
    try:
        with path.open("rb") as fh:
            return fh.read(5) == _PDF_MAGIC
    except OSError:
        return False


def _metadata_from_doc(doc) -> DocumentMetadata:
    """Pull the PDF info dictionary into a :class:`DocumentMetadata`."""
    meta = DocumentMetadata()
    info = {}
    try:
        info = doc.metadata or {}
    except Exception:  # pragma: no cover - metadata can be unreadable on bad files
        info = {}

    meta.raw = dict(info)
    meta.title = (info.get("title") or "").strip()
    meta.author = (info.get("author") or "").strip()
    meta.subject = (info.get("subject") or "").strip()
    meta.keywords = (info.get("keywords") or "").strip()
    meta.creator = (info.get("creator") or "").strip()
    meta.producer = (info.get("producer") or "").strip()
    meta.creation_date = (info.get("creationDate") or "").strip()
    meta.modified_date = (info.get("modDate") or "").strip()
    return meta


class PdfSource:
    """A thin, lazy wrapper around an opened PyMuPDF document.

    Kept intentionally small in M0. Later milestones add block extraction
    and OCR dispatch while reusing this handle.
    """

    def __init__(self, path: str | Path, doc=None):
        self.path = Path(path).expanduser().resolve()
        if doc is None:
            doc = pymupdf.open(self.path)
        self._doc = doc
        self._owns_doc = doc is None

    @classmethod
    def from_path(cls, path: str | Path) -> "PdfSource":
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"PDF not found: {p}")
        if not p.is_file():
            raise ValueError(f"Not a file: {p}")
        if not _looks_like_pdf(p):
            raise ValueError(f"File does not look like a PDF (bad magic bytes): {p}")
        try:
            return cls(p)
        except Exception as exc:  # PyMuPDF can raise on corrupt/encrypted PDFs
            raise ValueError(f"Could not open PDF '{p}': {exc}") from exc

    # --- introspection --------------------------------------------------- #
    @property
    def page_count(self) -> int:
        return int(self._doc.page_count)

    @property
    def doc(self):
        """The underlying opened PyMuPDF document (the live handle).

        Higher ingest steps (figures/tables in M2 C2, OCR in M3) that need the
        raw handle use this accessor rather than reaching into ``_doc``.
        """
        return self._doc

    def metadata(self) -> DocumentMetadata:
        return _metadata_from_doc(self._doc)

    def page_widths_heights(self) -> list[tuple[float, float]]:
        """Return (width, height) in points for each page."""
        out = []
        for page in self._doc:
            rect = page.rect
            out.append((float(rect.width), float(rect.height)))
        return out

    def read(self) -> ReadingDocument:
        """Open the document and return a :class:`ReadingDocument` (M0 view:
        identity + metadata + page count; blocks filled in by later steps)."""
        meta = self.metadata()
        doc = ReadingDocument(
            source_path=str(self.path),
            title=meta.title or self.path.stem,
            page_count=self.page_count,
            metadata=meta,
        )
        log.info("opened %s: %d page(s)", self.path.name, doc.page_count)
        return doc

    def close(self) -> None:
        if self._owns_doc:
            try:
                self._doc.close()
            except Exception:  # pragma: no cover
                pass

    def __enter__(self) -> "PdfSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_pdf(path: str | Path) -> ReadingDocument:
    """Convenience: open a PDF and return a :class:`ReadingDocument`.

    Validates the file first (existence + magic bytes) so callers get a clear
    error instead of a cryptic PyMuPDF traceback.
    """
    source = PdfSource.from_path(path)
    try:
        return source.read()
    finally:
        source.close()
