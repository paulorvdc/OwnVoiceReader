"""Ingest layer: turn a PDF file into the unified :class:`Block` shape.

Text extraction and OCR both feed the *same* shape so the reader never branches
on origin (BUILD_PLAN.md §4). The text path is wired end to end:
``pdf_source`` (open) -> ``text_extractor`` (lines + sparse flags) ->
``reading_order`` (ordered blocks). M3 adds the OCR path: pages whose text layer
is sparse (``flag_sparse_pages``) are rasterized and read by an
:class:`~ownvoicereader.ingest.ocr.OcrEngine`, which emits the *same*
``Line`` shape (tagged ``source=OCR``) so scanned and typed pages are read
together in one ``reading_order`` pass.
"""

from __future__ import annotations

import logging

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
from .ocr import (
    OcrEngine,
    OcrLine,
    RapidOcrEngine,
    TesseractEngine,
    NullOcrEngine,
    ocr_page,
    ocr_sparse_pages,
    build_ocr_engine,
)

log = logging.getLogger("ownvoicereader.ingest")

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
    # M3: OCR fallback for pages without a text layer.
    "OcrEngine",
    "OcrLine",
    "RapidOcrEngine",
    "TesseractEngine",
    "NullOcrEngine",
    "ocr_page",
    "ocr_sparse_pages",
    "build_ocr_engine",
    "build_document",
]


def build_document(doc: ReadingDocument, source: PdfSource | None = None,
                   ocr=None, ocr_enabled: bool = True) -> ReadingDocument:
    """Populate ``doc.blocks`` with a reading-ordered text sequence (M1) plus
    the page's figure/table blocks (M2 C2) plus OCR for any page lacking a text
    layer (M3).

    Opens the PDF (via ``source`` or a fresh handle from ``doc.source_path``),
    extracts text lines, flags sparse pages, and — unless disabled — runs OCR on
    those sparse pages so a *scanned* page reads the same as a typed one (the
    OCR lines are merged with the text lines and read together). Then it builds
    the ordered :class:`Block` list and extracts the figures/tables (skipping
    fully-scanned pages, whose only raster is the page itself). Returns the same
    ``doc`` for chaining.

    ``ocr`` is an :class:`~ownvoicereader.ingest.ocr.OcrEngine`; when omitted one
    is built on demand **only if there are sparse pages** (a text-only PDF pays
    nothing — no engine is constructed). ``ocr_enabled`` lets a caller (or a
    future ``--no-ocr`` flag) skip OCR entirely.
    """
    owns = source is None
    if owns:
        source = PdfSource.from_path(doc.source_path)
    try:
        geometry = source.page_widths_heights()
        lines = extract_lines(doc, source=source)
        doc.sparse_pages = flag_sparse_pages(lines, doc.page_count)

        # M3: read the sparse (scanned / image-only) pages via OCR and merge
        # their lines with the text layer so both are read in one pass.
        if ocr_enabled and doc.sparse_pages:
            engine = ocr if ocr is not None else build_ocr_engine()
            if getattr(engine, "available", False):
                ocr_lines = ocr_sparse_pages(doc, doc.sparse_pages, engine,
                                            source=source)
                lines.extend(ocr_lines)
                doc.ocr_pages = sorted({ln.page for ln in ocr_lines})
                log.info(
                    "OCR (%s): %d sparse page(s) -> %d line(s) on %d page(s)",
                    getattr(engine, "name", "?"), len(doc.sparse_pages),
                    len(ocr_lines), doc.page_count,
                )
            else:
                log.warning(
                    "OCR unavailable: %d sparse page(s) will be unreadable "
                    "(install the [ocr] extra: pip install 'ownvoicereader[ocr]')",
                    len(doc.sparse_pages),
                )

        doc.blocks = order_lines(lines, geometry, doc.page_count)
        # Figures + tables (M2 C2): appended after the reading-ordered text blocks.
        # Skip the OCR'd (fully-scanned) pages — their single raster is the whole
        # page (the text we just read), not a figure or table.
        add_figure_table_blocks(doc, source=source,
                                skip_pages=set(doc.ocr_pages))
    finally:
        if owns:
            source.close()
    return doc
