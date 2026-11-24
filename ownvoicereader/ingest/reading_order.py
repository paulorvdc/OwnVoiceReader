"""ingest/reading_order.py — M1: turn extracted lines into a reading-ordered
sequence of :class:`Block` objects.

This is the #1 correctness risk in BUILD_PLAN.md §5: multi-column papers
(academic PDFs are usually 2-column) must be read column-by-column, top→bottom,
left→right — not in PyMuPDF's raw layout order. The algorithm:

  1. For each page, detect the column boundaries with a *vertical projection*:
     bin the x-axis, and mark bins that any "narrow" line (body text, not a
     full-width header/figure) covers. Runs of *uncovered* bins are inter-column
     gutters; the gutters split the page into columns.
  2. Assign every line to a column by its horizontal center.
  3. Order lines by (column, y0) — i.e. read each column top→bottom, then the
     next column.
  4. Merge consecutive same-column lines into paragraph-sized :class:`Block`
     objects (a larger vertical gap starts a new block).

The detector is layout-agnostic: it yields 1 column when there is no gutter
(single-column book-like PDF) and N columns when there are N gutters, so it
works for 1-, 2-, 3- and 4-column pages without being told the count.
"""

from __future__ import annotations

import logging
import re

from ..model import Block, BBox
from .text_extractor import Line

log = logging.getLogger("ownvoicereader.ingest.order")

# Gutter detection tuning.
_GUTTER_MIN_FRACTION = 0.02   # a gutter must be >= 2% of page width to count
_GUTTER_COVERAGE_FRACTION = 0.10  # a "gutter" bin holds <=10% of peak coverage
_PROJ_BINS = 400             # vertical-projection resolution across the page width
# A new paragraph block starts when the vertical gap to the previous line exceeds
# this multiple of the local line height.
_PARAGRAPH_GAP_MULT = 1.6
# Fallback absolute gap (points) when line height is unknown.
_PARAGRAPH_GAP_ABS = 10.0
# A line wider than this fraction of the page is "full-width" (title, running
# header, abstract bar, full-width figure caption) and is excluded from the
# gutter projection so it doesn't fill in the inter-column gutters.
_FULL_WIDTH_FRACTION = 0.55
# A bare page number / marker: digits (and optional separators) only.
_DIGITS_RE = re.compile(r"[\d\-\.]+")


def _project(lines: list[Line], page_width: float) -> tuple[list[int], float, float]:
    """Vertical projection of *body* lines over x.

    Returns ``(coverage, peak, gutter_threshold)``. Stray short lines — page
    numbers and footnote markers like "9025" or "E‡" — are excluded because they
    often sit in the inter-column gutter and would otherwise hide it.
    """
    bin_w = page_width / _PROJ_BINS
    coverage = [0] * _PROJ_BINS
    for ln in lines:
        if ln.width >= _FULL_WIDTH_FRACTION * page_width:
            continue  # full-width element; excluded from the gutter projection
        if _is_page_number(ln.text):
            continue  # page numbers / markers sit in the gutter
        lo = max(0, int(ln.x0 / bin_w))
        hi = min(_PROJ_BINS - 1, int(ln.x1 / bin_w))
        for b in range(lo, hi + 1):
            coverage[b] += 1
    peak = max(coverage) if coverage else 0
    # A "gutter" bin is one whose coverage is a small fraction of the peak.
    # Strict zero is too brittle (a lone page number pokes the gutter); a
    # fraction of the peak cleanly separates a near-empty gutter from a column.
    threshold = max(1, int(_GUTTER_COVERAGE_FRACTION * peak))
    return coverage, float(peak), float(threshold)


def _is_page_number(text: str) -> bool:
    """True for page numbers / pure markers — the lines that intrude into the
    inter-column gutter and would otherwise hide it from the projection."""
    t = text.strip()
    if not t:
        return True
    if len(t) <= 2:
        return True  # "•", "1", "2": footnote / page markers
    return bool(_DIGITS_RE.fullmatch(t.replace(" ", "")))


def _is_stray(text: str) -> bool:
    """A line that is a page number, a lone section/footnote marker, etc.

    These are not readable prose and are frequently positioned in the column
    gutter; excluding them keeps the gutter detection robust.
    """
    t = text.strip()
    if not t:
        return True
    if len(t) <= 3:
        # "1", "2", "•", "E‡", "—": footnote / section markers.
        return True
    # A bare page number (optionally with a hyphen / dot).
    if _DIGITS_RE.fullmatch(t.replace(" ", "")):
        return True
    return False


def _detect_columns(lines: list[Line], page_width: float, page_height: float
                   ) -> list[tuple[float, float]]:
    """Return column slots ``[(left, right), ...]`` for a page.

    Uses a *threshold* vertical projection over x of the body lines: a run of
    low-coverage bins is an inter-column gutter. Returns a single
    ``[(0, page_width)]`` slot when no gutter is found (single column).
    """
    if page_width <= 0:
        return [(0.0, 1.0)]

    coverage, peak, threshold = _project(lines, page_width)
    if peak <= 0:
        # No body text at all: treat as single column (e.g. an image page).
        return [(0.0, page_width)]

    min_gutter_bins = max(1, int(_GUTTER_MIN_FRACTION * _PROJ_BINS))
    gutters: list[tuple[int, int]] = []  # (start_bin, end_bin)
    run_start = None
    for i, c in enumerate(coverage):
        if c <= threshold:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                if i - run_start >= min_gutter_bins:
                    gutters.append((run_start, i))
                run_start = None
    if run_start is not None and _PROJ_BINS - run_start >= min_gutter_bins:
        gutters.append((run_start, _PROJ_BINS))

    if not gutters:
        return [(0.0, page_width)]

    # Cap the column count to keep things sane on noisy pages.
    gutters = gutters[:3]  # => at most 4 columns

    bin_w = page_width / _PROJ_BINS
    slots: list[tuple[float, float]] = []
    prev = 0.0
    for (gs, ge) in gutters:
        left = prev
        right = gs * bin_w
        if right - left > 0:
            slots.append((left, right))
        prev = ge * bin_w
    if page_width - prev > 0:
        slots.append((prev, page_width))
    return slots or [(0.0, page_width)]


def _column_index(center_x: float, slots: list[tuple[float, float]]) -> int:
    """Assign a line's x-center to a column index (nearest slot if in a gutter)."""
    best_i = 0
    best_d = float("inf")
    for i, (lo, hi) in enumerate(slots):
        if lo <= center_x <= hi:
            return i
        d = min(abs(center_x - lo), abs(center_x - hi))
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _merge_into_blocks(column_lines: list[Line]) -> list[Block]:
    """Merge vertically-adjacent lines of one column into paragraph blocks."""
    if not column_lines:
        return []
    lines = sorted(column_lines, key=lambda ln: ln.y0)
    blocks: list[Block] = []
    cur_lines: list[Line] = []
    cur_gap_ref = 0.0

    def flush() -> None:
        if not cur_lines:
            return
        text = " ".join(ln.text for ln in cur_lines)
        x0 = min(ln.x0 for ln in cur_lines)
        y0 = min(ln.y0 for ln in cur_lines)
        x1 = max(ln.x1 for ln in cur_lines)
        y1 = max(ln.y1 for ln in cur_lines)
        blocks.append(
            Block.text_block(text, cur_lines[0].page, BBox(x0, y0, x1, y1))
        )

    for i, ln in enumerate(lines):
        if not cur_lines:
            cur_lines.append(ln)
            cur_gap_ref = ln.height or _PARAGRAPH_GAP_ABS
            continue
        gap = ln.y0 - cur_lines[-1].y1
        threshold = max(_PARAGRAPH_GAP_ABS, _PARAGRAPH_GAP_MULT * cur_gap_ref)
        if gap > threshold:
            flush()
            cur_lines = []
            cur_gap_ref = ln.height or _PARAGRAPH_GAP_ABS
        cur_lines.append(ln)
    flush()
    return blocks


def order_lines(
    lines: list[Line],
    page_geometry: list[tuple[float, float]],
    page_count: int,
) -> list[Block]:
    """Return text :class:`Block` objects in reading order across all pages.

    ``page_geometry`` is a list of ``(width, height)`` per page (from
    :meth:`PdfSource.page_widths_heights`).
    """
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)

    ordered: list[Block] = []
    for pno in range(page_count):
        page_lines = by_page.get(pno, [])
        if not page_lines:
            continue
        w, h = page_geometry[pno] if pno < len(page_geometry) else (0.0, 0.0)
        slots = _detect_columns(page_lines, w, h)
        # Assign each line to a column.
        by_col: dict[int, list[Line]] = {}
        for ln in page_lines:
            ci = _column_index(ln.center_x, slots)
            by_col.setdefault(ci, []).append(ln)
        # Read columns left → right; within a column top → bottom.
        for ci in sorted(by_col):
            ordered.extend(_merge_into_blocks(by_col[ci]))

    log.info("reading order: %d line(s) -> %d block(s) over %d page(s)",
             len(lines), len(ordered), page_count)
    return ordered
