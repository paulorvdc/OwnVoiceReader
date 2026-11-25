"""reader/sections.py — M1.5 checkpoint 2: section-aware navigation.

Detect a flat list of *sections* from a reading document's ordered, *spoken*
blocks, each with the cue-index range it owns, so the reader can:

  * snap skip / rollback to **section / subsection** boundaries instead of an
    arbitrary 200-char cue (A9), and
  * **announce** a section's name ("Section 3, Method") when the reader jumps in,
  * show a **"now reading: <section>"** indicator, and
  * count front matter (title / authors / abstract / …) as its own landmarks.

Detection is purely heuristic (A10) — font prominence, numbering, and known
front-matter / section keywords — so it stays fully offline. An LLM refine is
an opt-in flag for a later milestone, not used here.

The function operates on the *spoken* block list (after page-number / running-
header filtering), so ``block_start`` maps cleanly onto cue indices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..model import Block

# Section "kinds" — the landmarks the reader can navigate to / announce.
# Front matter (identity + abstract) first, then body sections, then back matter.
KIND_TITLE = "title"
KIND_AUTHORS = "authors"
KIND_ABSTRACT = "abstract"
KIND_KEYWORDS = "keywords"
KIND_CCS = "ccs"
KIND_SECTION = "section"
KIND_SUBSECTION = "subsection"
KIND_REFERENCES = "references"
KIND_ACKS = "acknowledgments"

# Kinds we speak a lead-in for when the reader *jumps* into the section. Title /
# authors / keywords / CCS / abstract are NOT announced: they are either the very
# first thing heard (no "you just jumped here" value) or already name themselves
# in their own text ("Abstract. …", "Keywords: …"), so a lead-in would only
# double up.
_ANNOUNCE_KINDS = frozenset(
    {
        KIND_SECTION,
        KIND_SUBSECTION,
        KIND_REFERENCES,
        KIND_ACKS,
    }
)

# A body line's "prominent" font is this multiple of the body font size or larger.
_PROMINENT_MULT = 1.12
# A heading is short: at most this many words.
_MAX_HEADING_WORDS = 12

# Numbered subsection: "3.2 Subtitle", "1.1.4 Deep".
_SUB_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)*)[.\s]+(\S.*)?$")
# Top-level numbered section: "1 Introduction" (number + Capitalized word).
_SECTION_RE = re.compile(r"^(\d+)\s+([A-Z]\S.*)$")
# "Appendix A …"
_APPENDIX_RE = re.compile(r"^(appendix\s+[A-Z]\.?\s+\S.*)$")
# A *numbered* heading label: "1 …", "3.2 …", "Appendix A …". Used to mark the
# start of the body (after the first numbered heading, an un-numbered prominent
# block may be a section; before it, a prominent page-0 block is more likely an
# author name in a 2-column layout than a heading).
_NUMBERED_RE = re.compile(r"^(?:\d+(?:\.\d+)*|appendix\s+[A-Z])\b", re.IGNORECASE)


def _is_numbered_label(label: str) -> bool:
    """True if ``label`` is a numbered section/subsection (``"1 …"``, ``"3.2 …"``,
    ``"Appendix A …"``) — the shape that unambiguously marks the body."""
    return bool(_NUMBERED_RE.match(label.strip()))
# Known un-numbered section names (start-of-line).
_NAMED_SECTIONS = (
    "introduction",
    "conclusion",
    "related work",
    "related works",
    "background",
    "discussion",
    "evaluation",
    "experiments",
    "results",
    "method",
    "methods",
    "overview",
    "abstract",
)


@dataclass
class Section:
    """One navigable landmark: a title, front-matter block, or section/subsection.

    ``block_start`` is the index of the section's first block *within the spoken
    block list*; ``cue_start`` / ``cue_end`` (one-past) are filled in by
    :func:`build_cues` once the blocks are split into cues, so the navigation
    can jump the playhead to ``cue_start``.
    """

    index: int
    kind: str
    label: str  # short label for "now reading" (e.g. "1 Introduction")
    block_start: int
    cue_start: int = -1  # filled by build_cues
    cue_end: int = -1  # one past the last cue of the section
    announce_label: str = ""  # spoken lead-in when jumping in ("" = don't announce)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "label": self.label,
            "cue_start": self.cue_start,
            "cue_end": self.cue_end,
            "announce": bool(self.announce_label),
        }


def _body_font_size(blocks: list[Block]) -> float:
    """Estimate the dominant *body* font size: the size carrying the most text.

    A robust estimate (char-weighted mode) that ignores a few large heading lines
    and lets a heading's prominence be measured *relative* to the body.
    """
    tally: dict[float, int] = {}
    for b in blocks:
        if b.font_size > 0:
            tally[round(b.font_size, 1)] = tally.get(round(b.font_size, 1), 0) + len(
                b.text
            )
    if not tally:
        return 0.0
    # The body is the size with the most characters.
    return max(tally, key=lambda s: tally[s])


def _heading_kind(block: Block, text: str, body_fs: float,
                  allow_prominent: bool = True) -> Optional[tuple[str, str]]:
    """Classify a *body* block as a section/subsection heading, or ``None``.

    Returns ``(kind, label)`` for a heading, else ``None`` (the block is ordinary
    body prose that belongs to the current section).

    A numbered / un-numbered *body* heading must be **prominent or bold** — a
    small-font "2 Méroué et al." running header matches the numbered shape but is
    body-sized, so it must NOT count. Front-matter / back-matter landmarks
    (Abstract / Keywords / CCS / References / Acknowledgments) are recognized by
    keyword alone, since they are unambiguous.

    ``allow_prominent`` gates the *prominent-but-unnumbered* rule: in the front
    matter (page 0, before the abstract) author names are set in a larger face and
    would otherwise be mistaken for sections, so the prominent rule is disabled
    there and only the unambiguous numbered / keyword rules apply.
    """
    low = text.lower().strip()
    words = len(text.split())
    prominent = body_fs > 0 and block.font_size >= body_fs * _PROMINENT_MULT
    standout = block.is_bold or prominent

    # Back-matter / front-matter landmarks by keyword (unambiguous).
    if low.startswith("acknowledg"):
        return KIND_ACKS, "Acknowledgments"
    if low.startswith("reference") or low.startswith("bibliography"):
        return KIND_REFERENCES, "References"

    # Numbered subsection "3.2 Subtitle" / top-level "1 Introduction" / "Appendix A".
    # Require prominence/bold so small-font running headers are excluded; these
    # numbered shapes are unambiguous even in the front matter.
    m = _SUB_RE.match(text)
    if m and words <= _MAX_HEADING_WORDS and standout:
        return KIND_SUBSECTION, text
    if _APPENDIX_RE.match(text) and words <= _MAX_HEADING_WORDS and standout:
        return KIND_SECTION, text
    m = _SECTION_RE.match(text)
    if m and words <= _MAX_HEADING_WORDS and standout:
        return KIND_SECTION, text

    # A prominent or bold *short* block that isn't front matter: a section name
    # set in a larger/bold face (e.g. "Introduction", "Related Work"). Only
    # outside the front matter, so author names (a larger face on page 0) are
    # not flagged as sections.
    if (
        allow_prominent
        and words <= 10
        and standout
        and not low.startswith(("abstract", "keywords", "ccs", "author"))
    ):
        return KIND_SECTION, text

    return None


def _announce_text(kind: str, label: str) -> str:
    """A spoken lead-in for a section, e.g. "Section 3, Method" / "References"."""
    if kind not in _ANNOUNCE_KINDS:
        return ""
    if kind in (KIND_SECTION, KIND_SUBSECTION):
        m = re.match(r"^(\d+(?:\.\d+)*)\s*(.*)$", label.strip())
        if m:
            num, rest = m.group(1), m.group(2).strip()
            return "Section " + num + ((", " + rest) if rest else "")
        # Un-numbered section (e.g. "Introduction"): just its name.
        return label
    return label  # "References", "Acknowledgments"


def is_heading(block: Block, text: str, body_fs: float) -> bool:
    """True if ``block`` (a *body* block) looks like a section/subsection heading
    — used to keep a genuine heading that happens to sit at the top of a later
    page from being mistaken for a running header."""
    return _heading_kind(block, text, body_fs) is not None


def detect_sections(blocks: list[Block]) -> list[Section]:
    """Detect the ordered sections over a *spoken* block list.

    Returns the section list; each section's ``cue_start`` / ``cue_end`` are left
    at -1 for :func:`build_cues` to fill. Detection order:

      1. The first spoken block is the **title**.
      2. A block starting "Abstract" / "Keywords" / "CCS Concepts" is its own
         front-matter section.
      3. Page-0 blocks before the abstract (names, affiliations, e-mails) form the
         **authors** section.
      4. Everything else starts a new **section/subsection** only when it looks
         like a heading (font / numbering / keyword); otherwise it joins the
         current section.
    """
    if not blocks:
        return []

    body_fs = _body_font_size(blocks)
    sections: list[Section] = []
    seen_abstract = False
    seen_numbered_section = False
    authors_started = False

    def new_section(i: int, kind: str, label: str) -> None:
        sec = Section(
            index=len(sections),
            kind=kind,
            label=label,
            block_start=i,
            announce_label=_announce_text(kind, label),
        )
        sections.append(sec)

    for i, block in enumerate(blocks):
        text = block.text.strip()
        low = text.lower().strip()

        if i == 0:
            # The first spoken block is the title (largest font, top of p0).
            new_section(i, KIND_TITLE, text)
            continue

        # Front-matter landmarks by keyword.
        if low.startswith("abstract"):
            new_section(i, KIND_ABSTRACT, "Abstract")
            seen_abstract = True
            continue
        if low.startswith("keywords"):
            new_section(i, KIND_KEYWORDS, "Keywords")
            continue
        if low.startswith("ccs concept") or low.startswith("ccs "):
            new_section(i, KIND_CCS, "CCS Concepts")
            continue

        # A *body* heading ends the front matter (and starts a body section) even
        # on page 0 — so a numbered "1 Introduction" is not swallowed into the
        # authors/front-matter region. Checked before the authors branch below.
        #
        # The *prominent-but-un-numbered* rule is gated to *after the body
        # begins*: a larger-face page-0 block before the first numbered heading is
        # far more likely an author name (a 2-column layout reads the right-column
        # authors after the abstract) than a section. Once the body has started, a
        # prominent block may be a heading. A *numbered* heading is still caught
        # here, since that shape is unambiguous even in the front matter.
        allow_prominent = block.page > 0 or seen_numbered_section
        hb = _heading_kind(block, text, body_fs, allow_prominent=allow_prominent)
        if hb is not None:
            kind, label = hb
            new_section(i, kind, label)
            if kind in (KIND_SECTION, KIND_SUBSECTION) and _is_numbered_label(label):
                seen_numbered_section = True
            continue

        # Authors + affiliations: page-0 blocks before the abstract (names,
        # institutions, e-mails). A heading was ruled out above.
        if not seen_abstract and block.page == 0:
            if not authors_started:
                new_section(i, KIND_AUTHORS, "Authors & institutions")
                authors_started = True
            continue  # remaining front-matter lines join the authors section

        # Otherwise it's ordinary body prose that joins the current section.

    # Fill cue_end for each section (the next section's block_start, or the end).
    # cue_start / cue_end are left at -1 for the orchestrator's _build to fill
    # once blocks are split into cues (a section's cue range is the next
    # section's cue_start .. end). detect_sections only establishes block_start.
    return sections


def section_for_block(sections: list[Section], block_index: int) -> Optional[Section]:
    """Return the section that owns ``block_index`` (the last whose range contains
    it). ``None`` for blocks that precede the first section (shouldn't happen)."""
    chosen = None
    for sec in sections:
        if sec.block_start <= block_index:
            chosen = sec
        else:
            break
    return chosen
