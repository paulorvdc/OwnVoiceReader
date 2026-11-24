"""Ingest layer: turn a PDF file into the unified :class:`Block` shape.

Text extraction and OCR both feed the *same* shape so the reader never branches
on origin (BUILD_PLAN.md §4). For M1 the text path is wired end to end:
``pdf_source`` (open) -> ``text_extractor`` (lines + sparse flags) ->
``reading_order`` (ordered blocks). OCR (M3) will join the same ``Line`` shape.
"""

from __future__ import annotations

from ..model import ReadingDocument
from .pdf_source import PdfSource, open_pdf, DocumentMetadata
from .text_extractor import Line, extract_lines, flag_sparse_pages, extract
from .reading_order import order_lines

__all__ = [
    "PdfSource",
    "open_pdf",
    "DocumentMetadata",
    "Line",
    "extract_lines",
    "flag_sparse_pages",
    "extract",
    "order_lines",
    "build_document",
]


def build_document(doc: ReadingDocument, source: PdfSource | None = None) -> ReadingDocument:
    """Populate ``doc.blocks`` with a reading-ordered text sequence (M1).

    Opens the PDF (via ``source`` or a fresh handle from ``doc.source_path``),
    extracts text lines, flags sparse pages, and builds the ordered
    :class:`Block` list. Returns the same ``doc`` for chaining.
    """
    owns = source is None
    if owns:
        source = PdfSource.from_path(doc.source_path)
    try:
        geometry = source.page_widths_heights()
        lines = extract_lines(doc, source=source)
        doc.sparse_pages = flag_sparse_pages(lines, doc.page_count)
        doc.blocks = order_lines(lines, geometry, doc.page_count)
    finally:
        if owns:
            source.close()
    return doc
