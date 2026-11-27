"""reader/timeline.py — M2 C4: the "right-moment" engine (A7).

Given the reading-ordered cue list and the :class:`ReferenceIndex` (M2 C1), this
decides *when* each figure/table is explained: at its **first prose reference**
(A7 default; the ``--director`` opt-in lets an LLM place them instead). This is
the *timing* layer.

The *content* of each explanation is composed here too, but is deliberately
**LLM-free** so the mechanism is hermetically testable without a model:

* **figures / tables with a caption** → speak the caption (A2 "caption + context"
  base): "Figure 3, Distribution of Consistency@10 across queries.";
* **without a caption** → a short context snippet (the sentence that references
  it). M2.5 layers a vision / LLM description on top when a vision model is
  present on the machine.

Firing model — a *pending* explanation queue, not a timeline index shift:
``build_explain_schedule`` maps each figure/table to the cue that *speaks its
first reference* (a merged block splits into several chunks, so we fire on the
chunk that actually contains the "…Fig. 3…" clause, not merely the block's
opening chunk). The orchestrator, when it speaks that cue, queues the explanation
to be spoken **immediately after** — the reader hears "…as shown in Fig. 3, the
results…" and then "Figure 3 shows …". Because the explanation is a pending
follow-on (it does not advance the cue playhead), section / cue navigation is
untouched.

Two refinements (M2.5):

* **An explanation may span several cues.** The composed text is split into
  TTS-sized chunks (a long vision description is not crammed into one cue), each
  spoken as its own cue / WAV. The split happens *at fire time* so a vision
  description that lands in the background is spoken in full.
* **Spoken markers bracket the explanation.** A start marker ("Now, a closer
  look at this figure.") announces the diversion from the prose, and an end
  marker ("Back to the text.") announces the return — spoken only when prose
  actually follows. Each is tagged ``meta.phase`` ("start" / "body" / "end") so
  the viewer can style them. The markers live here as pure phrasing; the
  orchestrator assembles the group (one shared start + end per referenced cue).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from ..model import BBox, Block, BlockKind, ReadingEventType
from ..reader.references import FigureTableEntry, ReferenceIndex, find_mentions

# `Cue` lives in orchestrator; orchestrator imports *this* module, so importing
# Cue at runtime would be circular. We only duck-type cue.block_id / cue.index,
# so the import is type-checking only (annotations are strings under PEP 563).
if TYPE_CHECKING:
    from .orchestrator import Cue

# A context snippet is only used when a figure/table has no caption; keep it short
# so the spoken aside stays brief and interruptible (A9).
_CONTEXT_MAX_CHARS = 160

_LABEL_WORD = {BlockKind.FIGURE: "Figure", BlockKind.TABLE: "Table"}


@dataclass
class ExplainItem:
    """One figure/table to explain at a given moment: its identity, its caption
    (or a context snippet), and the composed text to speak."""

    target: str
    kind: BlockKind
    label: str                       # "3", "3a", "2b", "I"
    label_word: str                  # "Figure" | "Table"
    caption: str = ""                # the caption text (sans the leading label)
    context: str = ""                # fallback snippet when there is no caption
    page: int = 0                   # the figure/table's page (for the highlight)
    bbox: Optional[BBox] = None
    vision: str = ""                # M2.5: the vision LLM's spoken description
    text: str = ""                  # the composed, spoken explanation

    @property
    def meta(self) -> dict:
        d = {"kind": "explain", "target": self.target, "label": self.label,
             "label_word": self.label_word, "page": self.page,
             "vision": bool(self.vision)}
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        return d


def _sentence(text: str) -> str:
    """End ``text`` with exactly one sentence terminator (no doubled period)."""
    text = text.strip()
    if not text:
        return text
    return text if text[-1] in ".!?" else text + "."


# A vision model often opens with a redundant self-reference ("This figure shows…",
# "The table lists…", "It shows…") that reads awkwardly after our own label
# ("Table 1, the table lists…"). We strip just that lead-in so "Table 1,
# <description>" flows. Two shapes: "<determiner> <visual-noun> <verb>" and a bare
# "<pronoun> <verb>". The verb set covers the openings these models actually use.
_VISION_VERBS = (
    r"shows?|depicts?|illustrates?|presents?|contains?|compares?|is|gives?|provides?|"
    r"displays?|lists?|reports?|includes?|holds?|covers?|summar(?:es|ises)?"
)
_VISION_LEAD = re.compile(
    r"^(?:"
    rf"(?:this|that|the|a|an)\s+(?:figure|fig\.?|table|tbl\.?|image|picture|chart|graph|diagram|plot)\s+(?:{_VISION_VERBS})\s+|"
    rf"(?:it|this|that|the)\s+(?:{_VISION_VERBS})\s+"
    r")",
    re.IGNORECASE,
)
# Bound a vision description to a spoken-friendly length (it may ramble).
_VISION_MAX_CHARS = 280


def _clean_vision(vision: str) -> str:
    """Tidy a raw vision answer into a short phrase that follows our label.

    Collapses whitespace, strips a redundant self-reference lead-in, and trims to
    a spoken-friendly length at a sentence/word boundary.
    """
    v = " ".join(vision.split()).strip()
    if not v:
        return v
    v = _VISION_LEAD.sub("", v, count=1)
    if len(v) > _VISION_MAX_CHARS:
        cut = v.rfind(". ", 0, _VISION_MAX_CHARS)
        if cut > _VISION_MAX_CHARS // 2:
            v = v[: cut + 1]
        else:
            cut = v.rfind(" ", 0, _VISION_MAX_CHARS)
            v = (v[:cut] if cut > 0 else v[:_VISION_MAX_CHARS]).rstrip() + "…"
    # Lower-case the first word so it reads as the continuation of "Figure 3, …".
    return v[0].lower() + v[1:] if v else v


def compose_explanation(item: ExplainItem) -> str:
    """Compose the spoken explanation.

    **M2.5 vision first:** when the vision LLM has produced a description
    (``item.vision``), speak *that* — it says what the figure/table actually shows
    — prefixed by its label ("Figure 3, a pipeline in which …"). Otherwise fall
    back to the **A2 base**: the caption when present, else a short context
    snippet ("…is referenced here: …"). Either way the output is a plain, short,
    sentence — no LaTeX, no markdown — so it reads naturally through a TTS engine.
    """
    lead = f"{item.label_word} {item.label}"
    if item.vision:
        v = _clean_vision(item.vision)
        return _sentence(f"{lead}, {v}")
    cap = " ".join(item.caption.split())
    if cap:
        # A caption is a title-case noun phrase ("Distribution of … across …").
        # Lower-case its first word so "Figure 3, distribution of …" reads as a
        # sentence rather than a heading.
        cap = cap[0].lower() + cap[1:] if cap else cap
        return _sentence(f"{lead}, {cap}")
    ctx = " ".join(item.context.split())
    if ctx:
        ctx = ctx[:_CONTEXT_MAX_CHARS]
        return _sentence(f"{lead} is referenced here: {ctx}")
    return f"{lead} is referenced here."


# --------------------------------------------------------------------------- #
# Explanation markers (M2.5): spoken bookends around a figure/table explanation
# --------------------------------------------------------------------------- #
# The primary user *cannot see the page*, so when the reader diverts from the
# prose to explain a figure/table it announces the diversion and the return in
# words: a start marker ("Now, a closer look at this figure.") before the body
# and an end marker ("Back to the text.") once the prose resumes. The markers are
# *spoken* and also carried in each event's ``meta.phase`` so the viewer can
# style them in the transcript. An explanation group (the figures/tables one cue
# references) shares *one* start and *one* end marker rather than one pair each —
# an interleaved two-column cue can reference several at once.
def start_marker_text(items: list[ExplainItem]) -> str:
    """The spoken lead-in before an explanation group's body.

    Singular/plural and figure/table-aware so the marker reads naturally for one
    figure, several figures, or a mixed group.
    """
    figs = any(i.kind is BlockKind.FIGURE for i in items)
    tabs = any(i.kind is BlockKind.TABLE for i in items)
    n = len(items)
    if figs and tabs:
        subject = "these figures and tables"
    elif figs:
        subject = "this figure" if n == 1 else "these figures"
    elif tabs:
        subject = "this table" if n == 1 else "these tables"
    else:  # pragma: no cover - a group is never empty when called
        subject = "this figure" if n == 1 else "these figures"
    return f"Now, a closer look at {subject}."


def end_marker_text() -> str:
    """The spoken lead-out, spoken before the prose resumes after an explanation."""
    return "Back to the text."


def _context_of(blocks: list[Block], block_id: str, raw: str) -> str:
    """The sentence containing ``raw`` in the block with id ``block_id`` (the
    context snippet used only when a figure/table has no caption)."""
    for b in blocks:
        if b.id != block_id:
            continue
        text = " ".join(b.text.split())
        # If we can isolate the sentence holding the reference, use it; else the
        # whole (short) block.
        m = re.search(re.escape(raw), text)
        if m:
            # back up to the previous sentence boundary, forward to the next one
            start = max(text.rfind(x, 0, m.start()) for x in ".!?")
            end = min(
                (p for p in (text.find(x, m.end()) for x in ".!?") if p >= 0),
                default=len(text),
            )
            return text[max(0, start + 1):end].strip()
        return text
    return ""


def build_explain_schedule(
    cues: list[Cue],
    blocks: list[Block],
    reference_index: ReferenceIndex,
) -> dict[int, list[ExplainItem]]:
    """Map each figure/table to the cue that **speaks its first prose reference**
    (the A7 "right moment").

    The reference is *not* necessarily in the cue that merely *opens* its block:
    a long merged block splits into several spoken chunks, and the "…as shown in
    Table 1…" clause can sit in a later chunk. So we fire on the cue whose text
    actually *contains* the reference — found by re-running the reference grammar
    on each cue, bounded to the reference's own block.

    Returns ``{cue_index: [ExplainItem, ...]}``. A figure/table with no prose
    reference (caption-only) is *not* scheduled — it has no reference moment to
    fire on (A7 fires on references, not on caption position).
    """
    # A missing/None index or an empty cue stream yields an empty schedule, never a
    # crash (the "degrade, never abort" contract the rest of the reader follows).
    if not reference_index or not cues:
        return {}
    blocks = blocks or []
    # Each block's cues are contiguous; record the first cue of each block (in
    # reading order) so a reference search can be bounded to its own block.
    block_first_cue: dict[str, int] = {}
    block_order: list[tuple[str, int]] = []
    for cue in cues:
        first = block_first_cue.setdefault(cue.block_id, cue.index)
        if first == cue.index:
            block_order.append((cue.block_id, cue.index))

    def first_cue_with(target: str, start: int, end: int) -> Optional[int]:
        for cue in cues[start:end]:
            if any(ref.target == target for ref in find_mentions(cue.text)):
                return cue.index
        return None

    schedule: dict[int, list[ExplainItem]] = {}
    for entry in reference_index.ordered():
        if not entry.has_prose_ref:
            continue
        start = block_first_cue.get(entry.first_ref_block_id)
        if start is None:
            continue  # referenced block was filtered out (e.g. a running header)
        # Bound the search to this block's own cue range (the next block's first
        # cue, or the end of the stream).
        end = len(cues)
        for _bid, first in block_order:
            if first > start:
                end = first
                break
        cue_idx = first_cue_with(entry.target, start, end)
        if cue_idx is None:
            # Rare: the grammar matched the *joined* block text but no single
            # chunk on its own — fire on the block's opening cue so the
            # explanation is not lost.
            cue_idx = start
        item = _item_from_entry(entry, blocks)
        schedule.setdefault(cue_idx, []).append(item)
    return schedule


def _item_from_entry(entry: FigureTableEntry, blocks: list[Block]) -> ExplainItem:
    item = ExplainItem(
        target=entry.target,
        kind=entry.kind,
        label=entry.label,
        label_word=_LABEL_WORD.get(entry.kind, "Figure"),
        caption=entry.caption or "",
        context=_context_of(blocks, entry.first_ref_block_id, entry.first_ref_raw),
        page=entry.page,
        bbox=entry.bbox,
    )
    item.text = compose_explanation(item)
    return item
