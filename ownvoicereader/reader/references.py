"""reader/references.py — M2 checkpoint 1: figure/table reference detection.

A paper's figures and tables are *introduced* by a **caption** block
("Figure 3: Stability analysis…") and *referenced* in the prose ("as shown in
Fig. 3", "Table 2(b) reports"). The reader should explain each figure/table at
its **first reference** — the *right moment* (A7) — not at the figure's own
location and not at every mention.

This module turns a reading-ordered list of :class:`Block` into a
:class:`ReferenceIndex`: one entry per figure/table, carrying its **caption** and
the reading-order position of its **first prose reference** (the place to fire an
``EXPLAIN``). It is *pure text* — no image, no LLM — so it is hermetically
testable and is the foundation the timeline (M2 C4) builds on.

Reference shapes handled (from the sample PDFs):
    Figure 3, Fig. 3, Fig 3, Fig. 3a, Fig. 3(a)      figures, arabic numbers
    Table 2, Table 2(b), Tbl. 2, TABLE I, TABLE II    tables; arabic or roman

Two hard-won constraints keep the grammar from over-matching:

* Roman numerals are **uppercase only** and capped at **XII** — otherwise an
  ordinary word ("Table in the results") is read as a number ("table:i").
* The label must end at a **non-letter boundary** and a panel is a *single*
  letter, so "Table in" / "Fig. 3abc" don't match while "Table 2(b)" /
  "Fig. 3a" do.

Caption vs prose (the one genuine ambiguity): a label at the *start* of a block
is usually its caption, but a sentence can also open with a reference
("Figure 6 displays the results"). We disambiguate by what *follows* the label:
a ``:`` / ``.`` or a following Capital is a caption (a title, "TABLE I OVERVIEW"),
a following lowercase char is a verb ("displays", "confirms") => a prose
reference, not a caption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..model import BBox, Block, BlockKind, Reference

# --------------------------------------------------------------------------- #
# Reference grammar
# --------------------------------------------------------------------------- #
# Label families: figures use Fig/Figure; tables use Table/Tbl/Tab (the
# case-insensitive flag also covers "TABLE").
_KINDS = r"(?:Fig(?:ure)?|Table|Tbl|Tab)"
# Roman numerals, I..XII only (academic figures/tables rarely exceed this),
# ordered longest-first so "XII" wins over "II"/"I".
_ROMAN = r"(?:XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)"
# A number: arabic digits, or an UPPERCASE roman numeral — case-scoped so an
# ordinary lowercase word ("in") can't masquerade as a roman "i".
_NUM = rf"(?:\d+|(?-i:{_ROMAN}))"
# An optional panel glued to the number: a single lowercase letter ("3a") or a
# parenthesised one ("2(b)"). One letter only, so "Table in" can't match.
_SUB = r"(?:\([a-c]\)|[a-c])?"
# The full label, ending at a non-letter boundary so a number glued to a word
# ("3abc") is rejected; the explicit panel is the one letter allowed after a
# number.
_LABEL_BODY = rf"(?P<kind>{_KINDS})\.?\s*(?P<num>{_NUM})(?P<sub>{_SUB})(?![a-zA-Z])"
# A label at the very start of a (stripped) block — used to spot captions.
_LABEL_RE = re.compile(rf"^{_LABEL_BODY}", re.IGNORECASE)
# A label *anywhere* in a block — a prose reference (the lookbehind stops
# mid-word / post-dot matches like "…Fig.3" glued to the previous token; the
# trailing (?![a-zA-Z]) in _LABEL_BODY stops a number glued to a word).
_MENTION_RE = re.compile(rf"(?<![\w.]){_LABEL_BODY}", re.IGNORECASE)

# A block longer than this that opens with a label is a prose sentence that
# happens to start with a reference, not a short caption title.
_CAPTION_MAX_CHARS = 140


def _normalize(kind_word: str, num: str, sub: str) -> tuple[BlockKind, str]:
    """Normalize a matched label into ``(kind, target)``.

    ``target`` is the canonical key the index keys on: ``"fig:3"``,
    ``"fig:3a"``, ``"table:2"``, ``"table:2b"``, ``"table:i"``. The display form
    ("3", "3a", "2b", "I") is derived from the target by :func:`_label_of`.
    """
    kind = BlockKind.FIGURE if kind_word.lower().startswith("fig") else BlockKind.TABLE

    # Number: arabic stays decimal; a roman numeral is lower-cased for the key
    # ("I" -> "i"). The display label upper-cases romans back (see _label_of).
    num_norm = str(int(num)) if num.isdigit() else num.lower()
    sub_norm = re.sub(r"[()]", "", sub or "").lower()

    prefix = "fig" if kind == BlockKind.FIGURE else "table"
    target = f"{prefix}:{num_norm}{sub_norm}"
    return kind, target


def find_mentions(text: str) -> list[Reference]:
    """Return every figure/table reference in ``text`` as :class:`Reference`.

    ``raw`` is the matched substring; ``target`` is the normalized key.
    ``page`` and ``block_id`` are left empty (the caller stamps them per block)."""
    out: list[Reference] = []
    for m in _MENTION_RE.finditer(text):
        kind, target = _normalize(m.group("kind"), m.group("num"), m.group("sub") or "")
        out.append(
            Reference(
                kind=kind,
                target=target,
                raw=m.group(0).strip(),
                page=-1,
                block_id="",
            )
        )
    return out


def caption_target(block: Block) -> Optional[tuple[str, str]]:
    """If ``block`` *is* a figure/table caption, return ``(target, caption_text)``.

    A caption is a short block that opens with a label followed by ``:`` / ``.``
    or a following Capital (a title like "TABLE I OVERVIEW …"). A prose sentence
    that merely starts with a reference ("Figure 6 displays …", a lowercase verb)
    is *not* a caption."""
    text = " ".join(block.text.split())
    m = _LABEL_RE.match(text)
    if not m:
        return None
    rest = text[m.end():].lstrip()
    if not rest:
        return None  # a bare "Figure 3" line is not a caption on its own

    first = rest[0]
    # ":" / "." right after the label is a caption separator; a following Capital
    # is a title-case caption. A lowercase char is a verb => prose, not a caption.
    is_caption = first in (":", ".") or first.isupper()
    if not is_caption or len(text) > _CAPTION_MAX_CHARS:
        return None

    _kind, target = _normalize(m.group("kind"), m.group("num"), m.group("sub") or "")
    caption = rest[1:].lstrip() if first in (":", ".") else rest
    return target, caption


# --------------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------------- #
@dataclass
class FigureTableEntry:
    """Everything the reader needs to explain one figure or table."""

    target: str
    kind: BlockKind
    label: str                       # "3", "3a", "2b", "I"
    caption: str = ""                # caption text, sans the leading label
    caption_block_id: str = ""
    page: int = 0
    bbox: Optional[BBox] = None
    first_ref_block_id: str = ""     # block holding the first *prose* reference
    first_ref_pos: int = -1          # reading-order position of that block (-1: none)
    first_ref_raw: str = ""          # the matched text, e.g. "Fig. 3"
    mention_count: int = 0           # total in-text mentions (incl. the caption)

    @property
    def has_prose_ref(self) -> bool:
        return self.first_ref_pos >= 0


@dataclass
class ReferenceIndex:
    """Map ``target`` -> :class:`FigureTableEntry`; ordered by first appearance."""

    entries: dict[str, FigureTableEntry] = field(default_factory=dict)

    def get(self, target: str) -> Optional[FigureTableEntry]:
        return self.entries.get(target)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries.values())

    def ordered(self) -> list[FigureTableEntry]:
        """Entries in the order their first reference (or caption) appears."""
        return sorted(
            self.entries.values(),
            key=lambda e: (e.first_ref_pos if e.has_prose_ref else 10 ** 9, e.page),
        )


def build_index(blocks: list[Block]) -> ReferenceIndex:
    """Build the :class:`ReferenceIndex` from a reading-ordered block list.

    Pass 1 finds **caption** blocks (a label that opens a short title-case block)
    and records each figure/table's caption + location. Pass 2 scans the *other*
    blocks for prose references and records, per target, the first one in reading
    order — that position is the "right moment" to explain.
    """
    idx = ReferenceIndex()
    caption_block_ids: set[str] = set()

    def get_or_create(target: str, kind: BlockKind) -> FigureTableEntry:
        e = idx.entries.get(target)
        if e is None:
            e = FigureTableEntry(target=target, kind=kind, label=_label_of(target))
            idx.entries[target] = e
        return e

    # Pass 1: captions.
    for b in blocks:
        if b.kind != BlockKind.TEXT:
            continue
        cap = caption_target(b)
        if cap is None:
            continue
        target, caption_text = cap
        caption_block_ids.add(b.id)
        e = get_or_create(target, _kind_of(target))
        if not e.caption:  # keep the first caption seen
            e.caption = caption_text
            e.caption_block_id = b.id
            e.page = b.page
            e.bbox = b.bbox
        e.mention_count += 1

    # Pass 2: prose references (skip blocks that are themselves captions).
    for i, b in enumerate(blocks):
        if b.kind != BlockKind.TEXT or b.id in caption_block_ids:
            continue
        for ref in find_mentions(b.text):
            e = get_or_create(ref.target, ref.kind)
            e.mention_count += 1
            if e.first_ref_pos < 0:  # first prose reference wins
                e.first_ref_block_id = b.id
                e.first_ref_pos = i
                e.first_ref_raw = ref.raw

    return idx


# --------------------------------------------------------------------------- #
# target helpers (kept tiny + local so the index stays self-contained)
# --------------------------------------------------------------------------- #
def _kind_of(target: str) -> BlockKind:
    return BlockKind.FIGURE if target.startswith("fig:") else BlockKind.TABLE


def _label_of(target: str) -> str:
    """'fig:3a' -> '3a', 'table:2b' -> '2b', 'table:i' -> 'I'."""
    rest = target.split(":", 1)[1] if ":" in target else target
    return rest.upper() if rest and rest.isalpha() else rest
