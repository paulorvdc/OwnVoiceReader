"""Ingest layer: turn a PDF file into the unified :class:`Block` shape.

Text and OCR both feed the same shape so the reader never branches on origin
(BUILD_PLAN.md section 4).
"""

from .pdf_source import PdfSource, open_pdf, DocumentMetadata

__all__ = ["PdfSource", "open_pdf", "DocumentMetadata"]
