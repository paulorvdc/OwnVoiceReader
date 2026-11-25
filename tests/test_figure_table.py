"""Tests for M2 checkpoint 2 — figure/table extraction.

Two kinds of coverage:

  * **Hermetic geometry** — the pure helpers (overlap test, region
    de-duplication, min-size filtering) that decide which raster regions count
    as a "figure" and collapse tiles of one figure into a single block. These
    need no PDF and pin the de-dup/area logic.
  * **Fixture extraction** — running the real extractors over the sample PDFs to
    confirm tables come out as cell grids and figures as cropped PNGs, with no
    crash on the pages that have neither.

The fixture tests use the real ``test_pdfs/`` samples (the extraction is
inherently PDF-based), so they are a little heavier than the pure-geometry ones,
but they pin the behaviour that matters: tables yield a grid, figures yield
image bytes, and empty pages yield nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ownvoicereader.ingest.figure_table import (
    _MIN_FIGURE_AREA,
    _figure_regions,
    _overlaps,
    add_figure_table_blocks,
    extract_figures,
    extract_tables,
)
from ownvoicereader.model import BBox, BlockKind
from ownvoicereader.ingest.pdf_source import PdfSource
from ownvoicereader.model import ReadingDocument

SAMPLES = [
    "1-column_springer.pdf",
    "2-column_acm-sigconf.pdf",
    "1-column_sbc.pdf",
    "2-column_ieee.pdf",
]
_PDF_DIR = Path(__file__).resolve().parent.parent / "test_pdfs"


# --------------------------------------------------------------------------- #
# Pure geometry
# --------------------------------------------------------------------------- #
def test_overlaps_true_for_contained_boxes():
    a = BBox(0, 0, 10, 10)
    b = BBox(2, 2, 8, 8)  # fully inside a
    assert _overlaps(a, b) is True


def test_overlaps_false_for_disjoint_boxes():
    a = BBox(0, 0, 10, 10)
    b = BBox(20, 20, 30, 30)
    assert _overlaps(a, b) is False


def test_overlaps_uses_fraction_threshold():
    # Two 10x10 boxes sharing a 40%-of-smaller overlap are "the same" figure;
    # a 10% overlap is not.
    big = BBox(0, 0, 100, 100)
    near = BBox(0, 0, 50, 50)        # 50% overlap -> same
    far = BBox(95, 95, 105, 105)    # ~1% overlap -> different
    assert _overlaps(big, near) is True
    assert _overlaps(big, far) is False


def test_figure_regions_drops_tiny_and_dedups():
    # A page with one big region, a tiny one (dropped), and an overlapping tile
    # of the big one (collapsed into it) -> a single figure region. Text blocks
    # (type 0) are ignored.
    data = {"blocks": [
        {"type": 1, "bbox": (100, 100, 400, 300)},   # big figure
        {"type": 1, "bbox": (10, 10, 20, 15)},        # tiny -> dropped
        {"type": 1, "bbox": (110, 110, 300, 250)},    # tile of big -> dedup
        {"type": 0, "bbox": (5, 5, 50, 10)},          # text block -> ignored
    ]}
    regions = _figure_regions(object(), data)
    assert len(regions) == 1, f"expected one merged figure, got {len(regions)}"
    r = regions[0]
    assert r.width >= 300 and r.height >= 200  # it enclosed both tiles
    assert r.width * r.height > _MIN_FIGURE_AREA  # the tiny one was dropped


def test_extract_tables_and_figures_on_empty_page():
    # A page with neither a table nor an image region yields no blocks, and does
    # not raise. A zero-page document likewise yields nothing.
    doc = ReadingDocument()
    doc.page_count = 0
    assert extract_tables(doc, _NullSource(0)) == []
    assert extract_figures(doc, _NullSource(0)) == []


class _NullSource:
    """A stand-in source whose pages yield no tables/images."""

    def __init__(self, page_count: int):
        self._n = page_count

    def __getitem__(self, pno: int):
        return _NullPage()


class _NullPage:
    def find_tables(self):
        class _R:
            tables = []
        return _R()

    def get_text(self, _mode):
        return {"blocks": []}


# --------------------------------------------------------------------------- #
# Fixture extraction (uses the real sample PDFs)
# --------------------------------------------------------------------------- #
def test_tables_yield_cell_grids():
    for name in SAMPLES:
        path = _PDF_DIR / name
        if not path.exists():
            pytest.skip(f"missing sample {name}")
        doc = ReadingDocument()
        doc.source_path = str(path)
        doc.page_count = PdfSource.from_path(path).page_count
        src = PdfSource.from_path(path)
        try:
            tables = extract_tables(doc, src)
        finally:
            src.close()
        # Every sample paper has at least one table.
        assert tables, f"{name}: expected at least one table"
        for t in tables:
            assert t.kind is BlockKind.TABLE
            assert t.table_grid is not None and t.table_grid[0], "grid must be non-empty"
            assert t.bbox is not None
            assert t.image is None  # tables carry a grid, not an image


def test_figures_yield_png_bytes():
    for name in SAMPLES:
        path = _PDF_DIR / name
        if not path.exists():
            pytest.skip(f"missing sample {name}")
        doc = ReadingDocument()
        doc.source_path = str(path)
        doc.page_count = PdfSource.from_path(path).page_count
        src = PdfSource.from_path(path)
        try:
            figures = extract_figures(doc, src)
        finally:
            src.close()
        for fig in figures:
            assert fig.kind is BlockKind.FIGURE
            assert fig.image is not None and fig.image[:8] == b"\x89PNG\r\n\x1a\n", "must be PNG"
            assert fig.table_grid is None  # figures carry an image, not a grid


def test_add_figure_table_blocks_appends():
    name = "1-column_sbc.pdf"
    path = _PDF_DIR / name
    if not path.exists():
        pytest.skip(f"missing sample {name}")
    doc = ReadingDocument()
    doc.source_path = str(path)
    doc.page_count = PdfSource.from_path(path).page_count
    before = len(doc.blocks)
    n = add_figure_table_blocks(doc)
    assert n > 0
    assert len(doc.blocks) == before + n
    # The appended blocks are only figures/tables, in the right shape.
    added = doc.blocks[before:]
    assert all(b.kind in (BlockKind.TABLE, BlockKind.FIGURE) for b in added)
