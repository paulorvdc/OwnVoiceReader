"""ingest/text_extractor.py — M1: extract a text *layer* from a PDF.

This is the text side of the "interchangeable" contract (BUILD_PLAN.md §4). It
produces a list of :class:`Line` (each a line of text with a page-space bbox and
font size) plus the set of *sparse* pages (little or no text layer — the kind
that will need OCR in M3). :mod:`reading_order` consumes the lines to build the
ordered :class:`Block` sequence the reader speaks.

Everything here is origin-agnostic: it only ever reads PyMuPDF's text layer.
OCR (M3) will feed the *same* :class:`Line` shape so downstream code never
branches on whether text came from a layer or from OCR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pymupdf  # PyMuPDF; `fitz` is a deprecated alias.

from ..model import ReadingDocument

log = logging.getLogger("ownvoicereader.ingest.text")

# A page with fewer extracted characters than this is "sparse" — it likely has a
# missing / trivial text layer and is a candidate for OCR (M3). 30 chars lets a
# page that is *almost* all a figure (a caption + a line) still count as text.
SPARSE_MIN_CHARS = 30


@dataclass
class Line:
    """One line of text with its page-space geometry.

    ``font_size`` and ``is_bold`` are captured so later milestones (M5 section
    detection, M2 caption sniffing) can use typographic cues without re-parsing.
    """

    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float = 0.0
    is_bold: bool = False

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    def bbox_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


def _iter_lines(page, page_no: int) -> list[Line]:
    """Yield :class:`Line` objects for a single PyMuPDF page."""
    lines: list[Line] = []
    try:
        data = page.get_text("dict")
    except Exception:  # pragma: no cover - a malformed page should not abort
        log.warning("could not parse page %d as text", page_no, exc_info=True)
        return lines

    for block in data.get("blocks", []):
        # block["type"] == 0 => text; 1 => image (figures, handled in M2).
        if block.get("type", 0) != 0:
            continue
        for ln in block.get("lines", []):
            raw = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            text = " ".join(raw.split())  # collapse internal whitespace
            if not text:
                continue
            x0, y0, x1, y1 = ln.get("bbox", (0, 0, 0, 0))
            # Representative font size / boldness of the line.
            size = 0.0
            bold = False
            for sp in ln.get("spans", []):
                size = max(size, sp.get("size", 0.0) or 0.0)
                if sp.get("font", "").lower().endswith(("bold", "black", "heavy")) or \
                   "bold" in sp.get("font", "").lower():
                    bold = True
            lines.append(
                Line(
                    page=page_no,
                    text=text,
                    x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1),
                    font_size=round(size, 2),
                    is_bold=bold,
                )
            )
    return lines


def extract_lines(doc: ReadingDocument, source=None) -> list[Line]:
    """Extract text lines for every page of ``doc``.

    ``source`` is an optional open :class:`PdfSource` (reused handle); when
    omitted we open the document's ``source_path``. Lines are returned in
    page order, each page's lines in layout order (top→bottom, left→right as
    PyMuPDF yields them — *not* yet reading order; that is reading_order's job).
    """
    if source is None:
        from .pdf_source import PdfSource

        source = PdfSource.from_path(doc.source_path)
        owns = True
    else:
        owns = False

    out: list[Line] = []
    try:
        fitz_doc = source._doc
        for pno in range(fitz_doc.page_count):
            out.extend(_iter_lines(fitz_doc[pno], pno))
    finally:
        if owns:
            source.close()
    return out


def page_char_counts(lines: list[Line]) -> dict[int, int]:
    """Total extracted characters per page (for sparse detection + stats)."""
    counts: dict[int, int] = {}
    for ln in lines:
        counts[ln.page] = counts.get(ln.page, 0) + len(ln.text)
    return counts


def flag_sparse_pages(lines: list[Line], page_count: int,
                      min_chars: int = SPARSE_MIN_CHARS) -> list[int]:
    """Return the sorted list of page numbers that have little/no text.

    A page is sparse if it contributed fewer than ``min_chars`` extracted
    characters. These are the pages that will be routed to OCR in M3.
    """
    counts = page_char_counts(lines)
    sparse = [p for p in range(page_count) if counts.get(p, 0) < min_chars]
    return sparse


def extract(doc: ReadingDocument, source=None) -> ReadingDocument:
    """Populate ``doc`` with text lines, sparse-page flags, and block stats.

    The ordered :class:`Block` sequence is produced by
    :func:`ownvoicereader.ingest.reading_order.order_lines`, which the caller
    (or :func:`build_document`) runs next.
    """
    lines = extract_lines(doc, source=source)
    doc.sparse_pages = flag_sparse_pages(lines, doc.page_count)
    log.info(
        "text extraction: %d lines, %d page(s), %d sparse page(s) %s",
        len(lines), doc.page_count, len(doc.sparse_pages),
        doc.sparse_pages or "(none)",
    )
    return doc
