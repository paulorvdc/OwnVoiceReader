"""llm/explainer.py — M2.5: the vision explainer.

The A2 base speaks a figure/table's *caption* (or a context snippet) at its first
reference. M2.5 layers a **vision** LLM (default ``lfm3-vl:2b``, a local Ollama
model via the ``ollama`` library) on top: the reader now speaks a short
description of *what the figure/table actually shows* — and for tables, *what the
data shows / its main finding* — instead of just the caption.

Design goals (BUILD_PLAN M2.5), kept deliberately simple:

  * **Vision for figures** — the figure's rendered image (already on the FIGURE
    block) goes to the VLM.
  * **Meaning for tables** — the table's rendered region *plus* its cell grid go
    to the VLM, which summarizes what the table compares and its main finding.
  * **Background** — descriptions are computed *ahead* of the reference (a thread
    started at load time) and cached on the :class:`ExplainItem`, so by the time
    the reader reaches the reference the description is usually ready. If it is not
    — vision off, slow, or the server down — the reader speaks the A2 base: it is
    **never silent and never blocked** (the graceful M6 "LLM-down" path).
  * **Local** — nothing leaves the machine; the model and server are the user's.

The explainer is *decoupled* from the firing mechanism (C4): it only fills
``item.vision`` and recomposes ``item.text`` via :func:`compose_explanation`. The
orchestrator emits whatever ``item.text`` currently is at fire time.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from ..model import Block, BlockKind, ReadingDocument
from ..reader.references import caption_target
from ..reader.timeline import ExplainItem, compose_explanation
from .client import LlmClient

log = logging.getLogger("ownvoicereader.llm.explainer")

# A table's grid is fed to the VLM as compact text so it has the actual numbers
# (a low-res render can miss a value). Bounded so a wide table does not blow the
# prompt.
_GRID_MAX_CHARS = 700
_GRID_MAX_ROWS = 40


# --------------------------------------------------------------------------- #
# Media linkage: target -> the FIGURE/TABLE block that carries its pixels/grid
# --------------------------------------------------------------------------- #
def link_media(blocks: list[Block]) -> dict[str, Block]:
    """Map each figure/table **target** to the :class:`Block` that holds its
    pixels (a FIGURE's rendered image) or cells (a TABLE's grid + region).

    A caption *text* block already resolves a target (M2 C1/C2), but it does not
    carry the image. We pair each media block with the **nearest caption on its
    own page** (captions always sit beside their figure/table) and take that
    caption's target. Two invariants keep a figure from borrowing a table's pixels
    (or vice versa) — the bug that mixed a figure's description onto a table:

      * **same kind.** A caption's target kind (`fig:` / `table:`) is fixed by its
        label ("Figure 3" vs "Table 2"), so a FIGURE block only ever matches a
        `fig:` caption and a TABLE block only a `table:` one. A figure can never be
        keyed under a table's target.
      * **2-D proximity.** The nearest caption is chosen by *x + y* distance, not y
        alone. A caption sits in the *same column* as its media, so an x-mismatch
        (a left-column table sharing a y-band with a right-column figure) must not
        win — y-only distance swapped them.

    Figures/tables whose caption is on a *different* page (a float) or that have
    none at all simply are not linked — their explanation falls back to the A2
    base. This is the "type-aware matching" the plan defers from C2; done here
    because *vision* (unlike the spoken base) needs the pixels.
    """
    # Caption text blocks on each page: (page, x-center, y-center, target, kind).
    captions: list[tuple[int, float, float, str, BlockKind]] = []
    for b in blocks:
        if b.kind is not BlockKind.TEXT or b.bbox is None:
            continue
        ct = caption_target(b)
        if ct is not None:
            target, _ = ct
            cx = (b.bbox.x0 + b.bbox.x1) / 2.0
            cy = (b.bbox.y0 + b.bbox.y1) / 2.0
            captions.append((b.page, cx, cy, target, _kind_of(target)))

    media_by_target: dict[str, Block] = {}
    for b in blocks:
        if b.kind not in (BlockKind.FIGURE, BlockKind.TABLE) or b.bbox is None:
            continue
        # Same page *and* same kind: a figure only ever matches a figure caption.
        same_kind = [c for c in captions if c[0] == b.page and c[4] is b.kind]
        if not same_kind:
            continue
        cx = (b.bbox.x0 + b.bbox.x1) / 2.0
        cy = (b.bbox.y0 + b.bbox.y1) / 2.0
        nearest = min(same_kind, key=lambda c: math.hypot(c[1] - cx, c[2] - cy))
        target = nearest[3]
        # Keep the first media block per target (the most prominent on the page).
        media_by_target.setdefault(target, b)
        b.caption = b.caption or target  # not used downstream; a tidy hint
    return media_by_target


def _kind_of(target: str) -> BlockKind:
    """'fig:3' -> FIGURE, 'table:2' -> TABLE (mirrors references._kind_of)."""
    return BlockKind.FIGURE if target.startswith("fig:") else BlockKind.TABLE


# --------------------------------------------------------------------------- #
# Prompts + grid text
# --------------------------------------------------------------------------- #
_FIGURE_PROMPT = (
    "You are a patient explainer reading a research paper aloud to a listener who "
    "cannot see the page. Describe this figure to them.\n"
    "{context}"
    "In one or two short spoken sentences, say what the figure shows and its main "
    "point. Be specific but concise. Do not use markdown, LaTeX, symbols, or "
    "citations. Do not begin with 'This figure' or 'The figure'."
)

_TABLE_PROMPT = (
    "You are a patient explainer reading a research paper aloud to a listener who "
    "cannot see the page. Explain this data table to them.\n"
    "{context}"
    "Here is the table's data (each row on its own line, cells separated by ' | '):\n"
    "{grid}\n\n"
    "In one or two short spoken sentences, say what the table compares and its main "
    "finding. Be specific but concise. Do not use markdown, LaTeX, symbols, or "
    "citations. Do not begin with 'This table' or 'The table'."
)


def _grid_text(grid: Optional[list[list]], max_chars: int = _GRID_MAX_CHARS,
               max_rows: int = _GRID_MAX_ROWS) -> str:
    """A compact, spoken-irrelevant text rendering of a table's cells for the
    VLM. Empty cells are dropped; the whole thing is length-bounded."""
    if not grid:
        return ""
    lines: list[str] = []
    for row in grid[:max_rows]:
        cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if cells:
            lines.append(" | ".join(cells))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    return text


def _context_line(kind: BlockKind, caption: str) -> str:
    label = "Figure" if kind is BlockKind.FIGURE else "Table"
    if caption:
        return f"Caption: {caption}\n"
    return f"{label} caption: (none detected)\n"


# --------------------------------------------------------------------------- #
# The explainer
# --------------------------------------------------------------------------- #
class Explainer:
    """Produces a short *spoken* vision description for a figure/table.

    Wrap a :class:`LlmClient` (an :class:`OllamaClient`, or a
    :class:`NullLlmClient` when vision is off). When the client is unavailable
    every :meth:`describe` returns ``""`` and the item keeps its A2 base — so the
    explainer is always safe to call.
    """

    def __init__(self, client: LlmClient, config=None):
        self.client = client
        self.config = config

    # -- one description --------------------------------------------------- #
    def describe(self, item: ExplainItem, block: Block, source=None) -> str:
        """Return a spoken vision description for ``item`` (its figure/table), or
        ``""`` when vision is unavailable / fails.

        Figures use the image already on the block; a table's region is rendered
        via ``source`` (and cached on the block) when not already present. The
        description is returned raw (untidied) — the caller stores it on
        ``item.vision`` and :func:`compose_explanation` tidies it.
        """
        if not self.client.available:
            return ""
        image = block.image
        if image is None and block.kind is BlockKind.TABLE and source is not None:
            image = self._render_table(block, source)

        if block.kind is BlockKind.FIGURE:
            # A figure is described from its pixels; no image, no description.
            if not image:
                return ""
            prompt = _FIGURE_PROMPT.format(
                context=_context_line(block.kind, item.caption or block.caption or ""))
            call = lambda: self.client.describe(image, prompt)
        else:
            # A table can be explained from its *rendered region* and/or its *grid
            # text*; the grid alone is enough when a region could not be rendered.
            grid = _grid_text(block.table_grid) or "(grid unavailable)"
            prompt = _TABLE_PROMPT.format(
                context=_context_line(block.kind, item.caption or block.caption or ""),
                grid=grid,
            )
            call = (lambda: self.client.describe(image, prompt)
                    if image else self.client.chat(
                        messages=[{"role": "user", "content": prompt}]))

        try:
            out = call()
        except Exception as exc:  # a vision failure must never abort the read
            log.warning("vision describe failed for %s: %s", item.target, exc)
            return ""
        return (out or "").strip()

    @staticmethod
    def _render_table(block: Block, source) -> Optional[bytes]:
        """Render a table's region to a PNG and cache it on the block."""
        from ..ingest.figure_table import render_region

        image = render_region(source, block.page, block.bbox)
        if image:
            block.image = image  # cache so a later call does not re-render
        return image

    # -- background batch --------------------------------------------------- #
    def precompute(self, schedule: dict[int, list[ExplainItem]],
                   media_by_target: dict[str, Block],
                   doc: Optional[ReadingDocument] = None,
                   source=None) -> int:
        """Fill ``item.vision`` (and recompose ``item.text``) for every scheduled
        explanation that has a linked media block. Returns how many got a vision
        description.

        Runs the slow vision calls for the *whole paper* — intended to be called
        from a background thread at load time so descriptions are ready by the
        reference. A media block with no pixels, or an unavailable/failing client,
        simply leaves the item on its A2 base (graceful).
        """
        filled = 0
        # One handle is enough to render every table region in the paper.
        owns = source is None
        src = source
        if owns and doc is not None and doc.source_path:
            try:
                from ..ingest.pdf_source import PdfSource

                src = PdfSource.from_path(doc.source_path)
            except Exception as exc:  # pragma: no cover
                log.warning("could not open source for vision render: %s", exc)
                src = None
        try:
            for items in schedule.values():
                for item in items:
                    block = media_by_target.get(item.target)
                    if block is None:
                        continue
                    vision = self.describe(item, block, source=src)
                    if vision:
                        item.vision = vision
                        item.text = compose_explanation(item)
                        filled += 1
        finally:
            if owns and src is not None:
                src.close()
        log.info("vision precompute: %d/%d explanation(s) enhanced",
                 filled, sum(len(v) for v in schedule.values()))
        return filled
