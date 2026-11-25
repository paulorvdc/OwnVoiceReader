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
from .figure_table import (
    add_figure_table_blocks,
    extract_tables,
    extract_figures,
    render_region,
)

__all__ = [
    "PdfSource",
    "open_pdf",
    "DocumentMetadata",
    "Line",
    "extract_lines",
    "flag_sparse_pages",
    "extract",
    "order_lines",
    "extract_tables",
    "extract_figures",
    "add_figure_table_blocks",
    "render_region",
    "build_document",
]


def build_document(doc: ReadingDocument, source: PdfSource | None = None) -> ReadingDocument:
    """Populate ``doc.blocks`` with a reading-ordered text sequence (M1) plus the
    page's figure/table blocks (M2 C2).

    Opens the PDF (via ``source`` or a fresh handle from ``doc.source_path``),
    extracts text lines, flags sparse pages, builds the ordered :class:`Block`
    list, then extracts the figures/tables (tables as a cell grid, figures as
    cropped PNGs) so the document is complete for both the *spoken* path (text
    cues + captions) and the *display* / *vision* path (M5 / M2.5). Returns the
    same ``doc`` for chaining.
    """
    owns = source is None
    if owns:
        source = PdfSource.from_path(doc.source_path)
    try:
        geometry = source.page_widths_heights()
        lines = extract_lines(doc, source=source)
        doc.sparse_pages = flag_sparse_pages(lines, doc.page_count)
        doc.blocks = order_lines(lines, geometry, doc.page_count)
        # Figures + tables (M2 C2): appended after the reading-ordered text blocks.
        add_figure_table_blocks(doc, source=source)
    finally:
        if owns:
            source.close()
    return doc
