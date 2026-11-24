"""Tests for the M0 ingest layer (pdf_source).

These are hermetic: they generate a tiny PDF with PyMuPDF so they do not depend
on the gitignored sample in test_pdfs/.
"""

from __future__ import annotations

import pytest

from ownvoicereader.ingest import open_pdf
from ownvoicereader.ingest.pdf_source import PdfSource, _looks_like_pdf
from ownvoicereader.model import ReadingDocument, DocumentMetadata


def _make_pdf(path, pages: int = 3) -> None:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} of {pages}\nHello, OwnVoiceReader.",
                         fontsize=12)
    doc.set_metadata({"title": f"Fixture {pages}", "author": "OwnVoiceReader Test"})
    doc.save(str(path))
    doc.close()


def test_open_pdf_returns_document(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=3)

    doc = open_pdf(str(pdf))
    assert isinstance(doc, ReadingDocument)
    assert doc.page_count == 3
    assert doc.title  # non-empty
    assert doc.metadata.author == "OwnVoiceReader Test"
    assert isinstance(doc.metadata, DocumentMetadata)


def test_page_geometry(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=2)
    with PdfSource.from_path(pdf) as src:
        wh = src.page_widths_heights()
        assert len(wh) == 2
        for w, h in wh:
            assert w > 0 and h > 0


def test_bad_magic_rejected(tmp_path):
    bad = tmp_path / "not.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(ValueError):
        PdfSource.from_path(bad)
    assert _looks_like_pdf(bad) is False


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        PdfSource.from_path(tmp_path / "does-not-exist.pdf")
