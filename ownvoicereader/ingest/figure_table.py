"""ingest/figure_table.py — M2 checkpoint 2: extract figures and tables.

The reader explains each figure/table at its *first prose reference* (M2 C1 finds
the reference and its caption; C4 fires the EXPLAIN there). To *render* one in the
viewer — and to feed the optional M2.5 vision pass — we also need its pixels
(figures) or its cells (tables). This module fills that in by populating
``doc.blocks`` with :class:`Block` objects of kind ``FIGURE`` / ``TABLE``:

  * **Tables** come from PyMuPDF's ``find_tables()`` — a text grid plus a
    page-space bounding box.
  * **Figures** are the page's embedded image regions; we crop each region to a
    PNG with ``get_pixmap``. (Pure-vector figures have no raster to crop, so we
    render whatever raster the page actually carries.) Overlapping regions are
    de-duplicated and tiny ones dropped, so a page littered with icons does not
    become a page of "figures."

These blocks are appended *after* the reading-ordered text blocks. The reader's
spoken path is untouched: cue-building and the reference index skip non-text
blocks (the orchestrator speaks only ``TEXT`` cues, and ``build_index`` scans only
text blocks for captions). The figure/table blocks are for **display** (the
viewer) and **vision** (M2.5) — their captions are carried on the blocks for a
self-describing document.
"""

from __future__ import annotations

import logging
from typing import Optional

import pymupdf  # PyMuPDF; `fitz` is a deprecated alias.
from pymupdf import Rect

from ..model import BBox, Block, BlockKind, ReadingDocument

log = logging.getLogger("ownvoicereader.ingest.figtab")

# Render figures at this resolution: enough for a viewer thumbnail and the M2.5
# vision pass, small enough to keep the pipeline fast and the PNGs modest.
_FIGURE_DPI = 150
# Drop image regions smaller than this (area in points^2) — footnotes, logos,
# bullet glyphs, and plot ticks are not "figures."
_MIN_FIGURE_AREA = 4_000.0
# Two image regions whose boxes overlap by more than this fraction of the smaller
# are the *same* figure (a figure split across several raster tiles); keep one.
_DEDUP_OVERLAP = 0.5


def _overlaps(a: BBox, b: BBox, frac: float = _DEDUP_OVERLAP) -> bool:
    """True if ``a`` and ``b`` overlap by more than ``frac`` of the smaller box."""
    ix = min(a.x1, b.x1) - max(a.x0, b.x0)
    iy = min(a.y1, b.y1) - max(a.y0, b.y0)
    if ix <= 0 or iy <= 0:
        return False
    inter = ix * iy
    smaller = min(a.width * a.height, b.width * b.height)
    return smaller > 0 and inter / smaller >= frac


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def extract_tables(doc: ReadingDocument, source, skip: Optional[set[int]] = None) -> list[Block]:
    """Return one :class:`Block` (``TABLE``) per table found on every page.

    ``source`` is the open :class:`PdfSource`; ``find_tables()`` yields each
    table's cell grid and its page-space bbox (a *tuple* in PyMuPDF 2.1); a
    table with no cells is skipped. ``skip`` (optional) is a set of page numbers
    to ignore — used by :func:`add_figure_table_blocks` to skip fully-scanned
    pages, whose single raster is the whole page (the text we OCR), not a table.
    """
    skip = skip or set()
    out: list[Block] = []
    for pno in range(doc.page_count):
        if pno in skip:
            continue
        page = source.doc[pno]
        try:
            tables = page.find_tables().tables
        except Exception:  # pragma: no cover - a malformed page must not abort
            log.warning("find_tables failed on page %d", pno, exc_info=True)
            continue
        for t in tables:
            grid = t.extract() or []
            grid = [[("" if c is None else c) for c in row] for row in grid]
            if not grid:
                continue
            bbox = BBox(*[float(x) for x in t.bbox])  # t.bbox is a tuple
            out.append(Block.table_block(grid, pno, bbox, caption=None))
    return out


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def extract_figures(doc: ReadingDocument, source, skip: Optional[set[int]] = None) -> list[Block]:
    """Return one :class:`Block` (``FIGURE``) per image region, cropped to a PNG.

    Each embedded image region of a page is cropped with ``get_pixmap`` (the
    vector-only case has no raster, so we render whatever raster the page
    carries). Overlapping regions collapse to one figure and tiny regions are
    dropped, so the output is the page's *figures*, not its icon soup. ``skip``
    (optional) is a set of page numbers to ignore — fully-scanned pages, whose
    single raster is the whole page rather than a figure.
    """
    skip = skip or set()
    out: list[Block] = []
    for pno in range(doc.page_count):
        if pno in skip:
            continue
        page = source.doc[pno]
        try:
            data = page.get_text("dict")
        except Exception:  # pragma: no cover
            log.warning("get_text failed on page %d", pno, exc_info=True)
            continue
        for region in _figure_regions(page, data):
            try:
                pix = page.get_pixmap(clip=region, dpi=_FIGURE_DPI)
                image = pix.tobytes("png")
            except Exception:  # pragma: no cover - a bad region must not abort
                log.warning("could not render a figure on page %d", pno, exc_info=True)
                continue
            if not image:
                continue
            out.append(Block.figure_block(image, pno, region, caption=None))
    return out


def _figure_regions(page, data: dict) -> list[Rect]:
    """The image regions of a page, de-duplicated and min-size filtered."""
    raw: list[Rect] = []
    for block in data.get("blocks", []):
        # block type 1 == an image; its bbox is the image's placement on the page.
        if block.get("type", 0) != 1:
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        rect = Rect(*[float(x) for x in bbox])
        if rect.width * rect.height < _MIN_FIGURE_AREA:
            continue  # too small to be a figure
        raw.append(rect)

    # Collapse overlapping regions: a figure split across several raster tiles is
    # one figure. Keep the first region that is not already covered by an earlier
    # (kept) one; grow that kept region to enclose the new one.
    kept: list[Rect] = []
    for r in raw:
        for k in kept:
            if _overlaps(BBox(*k), BBox(*r)):
                kept[kept.index(k)] = Rect(
                    min(k.x0, r.x0), min(k.y0, r.y0),
                    max(k.x1, r.x1), max(k.y1, r.y1),
                )
                break
        else:
            kept.append(r)
    return kept


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def add_figure_table_blocks(doc: ReadingDocument, source=None,
                            skip_pages: Optional[set[int]] = None) -> int:
    """Populate ``doc.blocks`` with the page's TABLE and FIGURE blocks.

    Runs *after* the reading-ordered text blocks so figures/tables sit in the
    document in a sensible place. Returns the number of figure/table blocks
    added. ``source`` is the open :class:`PdfSource`; when omitted one is opened
    from ``doc.source_path`` and closed afterwards. ``skip_pages`` (optional) is
    a set of page numbers to ignore — the OCR'd (fully-scanned) pages, whose only
    raster is the whole page rather than a figure or table.
    """
    owns = source is None
    if owns:
        from .pdf_source import PdfSource
        source = PdfSource.from_path(doc.source_path)

    try:
        added = (extract_tables(doc, source, skip=skip_pages)
                 + extract_figures(doc, source, skip=skip_pages))
    finally:
        if owns:
            source.close()

    doc.blocks.extend(added)
    n_tables = sum(1 for b in added if b.kind is BlockKind.TABLE)
    n_figs = sum(1 for b in added if b.kind is BlockKind.FIGURE)
    log.info(
        "figure/table extraction: %d table(s), %d figure(s) over %d page(s)",
        n_tables, n_figs, doc.page_count,
    )
    return len(added)


def render_region(source, page: int, bbox, dpi: int = _FIGURE_DPI) -> Optional[bytes]:
    """Rasterise the page-space region ``bbox`` on ``page`` to PNG bytes.

    The single place page regions are rendered: figure extraction crops its image
    regions here, and the M2.5 explainer uses it to render a *table* region (the
    TABLE block carries a grid but no image). ``bbox`` is a :class:`BBox` or a
    4-tuple. Returns ``None`` on a render failure, so the caller degrades instead
    of aborting.
    """
    try:
        coords = bbox.to_tuple() if hasattr(bbox, "to_tuple") else bbox
        rect = Rect(*[float(x) for x in coords])
        pix = source.doc[page].get_pixmap(clip=rect, dpi=dpi)
        return pix.tobytes("png")
    except Exception:  # pragma: no cover - a bad region must not abort the read
        log.warning("could not render a region on page %d", page, exc_info=True)
        return None
