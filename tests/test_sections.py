"""Tests for M1.5 checkpoint 2 — section-aware navigation (hermetic).

These pin the section-detection + navigation logic that the "skip to next
section" / "roll back to section" controls depend on:

  * front matter (title / authors / abstract / keywords) is detected as its own
    landmarks and read in order;
  * a *variant* author running header on a later page is dropped by role+position
    (not just by exact-string dedup) and never becomes a section;
  * numbered / un-numbered headings become sections, but a body-sized running
    header ("2 Meroue et al.") does not, and author names in the front matter do
    not (the ``allow_prominent`` gate);
  * ``next_section`` / ``prev_section`` move the playhead to section starts by
    cue index and flag the spoken lead-in.

The geometry is fed as synthetic :class:`Line` / :class:`Block` objects so it is
deterministic and needs no network, sample, or TTS engine.
"""

from __future__ import annotations

import asyncio

from ownvoicereader.ingest.reading_order import order_lines, _MARKER_RE
from ownvoicereader.ingest.text_extractor import Line
from ownvoicereader.model import Block, BBox, ReadingDocument
from ownvoicereader.reader.orchestrator import (
    _build,
    build_cues,
    Orchestrator,
)
from ownvoicereader.reader.sections import (
    detect_sections,
    is_heading,
    _body_font_size,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def L(page, text, x0, y0, x1=None, y1=None, fs=11.0):
    x1 = x1 if x1 is not None else x0 + 200
    y1 = y1 if y1 is not None else y0 + fs
    return Line(page=page, text=text, x0=x0, y0=y0, x1=x1, y1=y1, font_size=fs)


def B(text, page, x0, y0, x1=None, y1=None, fs=9.0, bold=False):
    """A synthetic body block at a fixed geometry (for section detection)."""
    x1 = x1 if x1 is not None else x0 + 500
    y1 = y1 if y1 is not None else y0 + fs
    return Block.text_block(text, page, BBox(x0, y0, x1, y1),
                            font_size=fs, is_bold=bold)


def _doc_with(blocks):
    d = ReadingDocument()
    d.blocks = blocks
    d.page_count = max((b.page for b in blocks), default=0) + 1
    return d


# A small, fully-known document: front matter + three numbered sections + refs.
def _sectioned_doc():
    blocks = [
        B("The Great Paper Title", 0, 50, 60, fs=17),
        B("Jane A. Author", 0, 50, 110, fs=11),
        B("University of Nowhere, Someplace", 0, 50, 124, fs=9),
        B("Abstract. This paper does a thing and shows it works well on data.", 0, 50, 160),
        B("1 Introduction", 0, 50, 200, fs=11, bold=True),
        B("Introduction body sentence one here for the reader to hear and follow.", 0, 50, 216),
        B("Introduction body sentence two continues the thought stated above it.", 0, 50, 230),
        B("2 Method", 1, 50, 100, fs=11, bold=True),
        B("Method body sentence describing the approach in detail here now.", 1, 50, 116),
        B("3 Results", 1, 50, 140, fs=11, bold=True),
        B("Results body sentence reporting the numbers we measured in here.", 1, 50, 156),
        B("References", 2, 50, 100, fs=11, bold=True),
        B("Some reference one here with an author and a year and a title in it.", 2, 50, 116),
    ]
    return _doc_with(blocks)


# --------------------------------------------------------------------------- #
# Detection: front matter + numbered sections + back matter
# --------------------------------------------------------------------------- #
def test_detect_sections_front_matter_and_body():
    cues, sections = _build(_sectioned_doc())
    kinds = [s.kind for s in sections]
    # Front matter first, in order, then the body sections, then back matter.
    assert kinds[0] == "title"
    assert kinds[1] == "authors"
    assert "abstract" in kinds
    assert "references" in kinds
    # The author name did NOT spawn its own section (the front-matter gate).
    labels = [s.label for s in sections]
    assert "Jane A. Author" not in labels
    # The numbered sections are detected with their labels.
    by_label = {s.label: s for s in sections}
    for label in ("1 Introduction", "2 Method", "3 Results"):
        assert label in by_label, labels
    # Each section's cue range is contiguous and ordered.
    for a, b in zip(sections, sections[1:]):
        assert a.cue_start < b.cue_start, (a.label, b.label, a.cue_start, b.cue_start)
        assert a.cue_end <= b.cue_start
    # "1 Introduction" owns a span of cues (heading + its two body cues).
    intro = by_label["1 Introduction"]
    assert intro.cue_end - intro.cue_start >= 3


def test_section_cues_are_stamped():
    """Every cue carries its section index/label/kind so the UI can show it."""
    cues, sections = _build(_sectioned_doc())
    by_idx = {s.index: s for s in sections}
    # A body cue inside "2 Method" must report that section.
    method = by_idx[next(s.index for s in sections if s.label == "2 Method")]
    stamped = [c for c in cues if c.section_index == method.index]
    assert stamped, "no cue stamped with the 2 Method section"
    assert all(c.section_label == "2 Method" for c in stamped)
    # Only the section-start cue carries a lead-in; the rest are spoken plainly.
    assert stamped[0].lead_in == "Section 2, Method"
    assert all(c.lead_in == "" for c in stamped[1:])
    start = [c for c in cues if c.lead_in]
    assert any(c.section_label == "1 Introduction" for c in start)


# --------------------------------------------------------------------------- #
# Front-matter / running-header de-duplication
# --------------------------------------------------------------------------- #
def test_variant_author_running_header_dropped():
    """A *variant* author running header (not an exact dup) is dropped by
    role+position so it is neither spoken nor mistaken for a section."""
    blocks = [
        B("Title Here", 0, 50, 60, fs=17),
        B("Jane A. Author", 0, 50, 110, fs=11),
        B("Abstract. Some abstract text that is reasonably long to be read out.", 0, 50, 150),
        B("Jane A. Author, John B. Coauthor", 1, 50, 60, fs=9),   # variant running header
        B("1 Introduction", 1, 50, 100, fs=11, bold=True),
        B("Body sentence of the introduction here for the reader to hear.", 1, 50, 116),
    ]
    cues = build_cues(_doc_with(blocks))
    joined = " ".join(c.text for c in cues)
    # The variant running header is dropped; the real page-0 author line survives.
    assert "Jane A. Author, John B. Coauthor" not in joined
    assert "Jane A. Author" in joined
    # It also did not become a section.
    _, sections = _build(_doc_with(blocks))
    labels = [s.label for s in sections]
    assert "Jane A. Author, John B. Coauthor" not in labels
    assert "1 Introduction" in labels


def test_body_sized_running_header_is_not_a_section():
    """A small-font "2 Meroue et al." running header is not a numbered section,
    even though it matches the numbered shape — only prominence/bold counts."""
    body_fs = 9.0
    header = B("2 Meroue et al.", 1, 50, 92, fs=9)   # body-sized, top band
    assert is_heading(header, "2 Meroue et al.", body_fs) is False
    # The same shape, but a real (prominent or bold) heading, is detected.
    heading = B("3 Method", 1, 50, 100, fs=11, bold=True)
    assert is_heading(heading, "3 Method", body_fs) is True


def test_allow_prominent_gate_blocks_front_matter_authors():
    """In the front matter (page 0, before the abstract) a large-font author name
    is NOT a section; the same shape outside the front matter IS."""
    body_fs = 9.0
    author = B("Paulo do Carmo", 0, 50, 110, fs=11.9)  # prominent, page 0
    # allow_prominent=False (front matter) => not a section.
    from ownvoicereader.reader.sections import _heading_kind
    assert _heading_kind(author, "Paulo do Carmo", body_fs, allow_prominent=False) is None
    # allow_prominent=True (body / later page) => a short prominent block is a section.
    got = _heading_kind(author, "Paulo do Carmo", body_fs, allow_prominent=True)
    assert got is not None and got[0] == "section"


# --------------------------------------------------------------------------- #
# The merge stage separates front matter + headings from body
# --------------------------------------------------------------------------- #
def test_font_and_marker_split_separates_units():
    """A title, an author line, an "Abstract." marker, and a "1 Introduction"
    heading each become their own block even with a small vertical gap, because
    a font-size change or a marker forces a split."""
    lines = [
        L(0, "The Great Paper Title", 50, 60, 550, 78, fs=17),
        L(0, "Jane A. Author", 50, 90, 300, 102, fs=11),       # font break 17->11
        L(0, "Abstract. Some abstract text here now.", 50, 110, 550, 119, fs=9),  # marker
        L(0, "The approach is then described in detail here now.", 50, 122, 550, 131, fs=9),
        L(0, "1 Introduction", 50, 140, 250, 154, fs=11),  # marker + font break
        L(0, "Introduction body sentence here for the reader now.", 50, 156, 550, 165, fs=9),
    ]
    blocks = order_lines(lines, page_geometry=[(612, 792)], page_count=1)
    texts = [b.text for b in blocks]
    # The title is its own block (font break 17->11).
    titles = [t for t in texts if t.startswith("The Great Paper Title")]
    assert len(titles) == 1 and "Jane" not in titles[0]
    # "Abstract." starts a new block (marker); its continuation joins it.
    abstracts = [t for t in texts if t.startswith("Abstract.")]
    assert len(abstracts) == 1
    assert "The approach is then described" in abstracts[0]
    # "1 Introduction" is its own block (marker + font break), separate from body.
    heads = [t for t in texts if t.startswith("1 Introduction")]
    assert len(heads) == 1 and "body sentence" not in heads[0]


def test_marker_regex_matches_known_openers():
    for opener in ("Abstract.", "Keywords", "CCS Concepts", "References",
                   "Acknowledgments", "1 Introduction", "3.2 Subtitle",
                   "Appendix A Extra"):
        assert _MARKER_RE.match(opener), opener
    # A body sentence that starts with a number is NOT a marker opener.
    assert _MARKER_RE.match("10 experiments were run") is None


# --------------------------------------------------------------------------- #
# Navigation: next_section / prev_section move the playhead by cue index
# --------------------------------------------------------------------------- #
def test_next_section_moves_playhead_to_section_starts():
    orch = Orchestrator(_sectioned_doc(), None)
    secs = orch.sections
    by_label = {s.label: s for s in secs}
    assert by_label["1 Introduction"].cue_start == 4
    assert by_label["2 Method"].cue_start == 7
    assert by_label["3 Results"].cue_start == 9
    assert by_label["References"].cue_start == 11

    # From the very start, jump forward lands on the next section (authors).
    assert orch.next_section() == 1            # authors start
    # Jump into "1 Introduction" flags the spoken lead-in.
    orch.pos = 3                               # at "Abstract"
    p = orch.next_section()
    assert p == 4                              # "1 Introduction" start
    assert orch._announce_next is True

    # Walking the sections forward by cue index.
    orch.pos = 4
    assert orch.next_section() == 7            # -> "2 Method"
    assert orch.next_section() == 9            # -> "3 Results"
    assert orch.next_section() == 11           # -> "References"
    # At the last section, a further forward jump is a no-op.
    assert orch.next_section() == 11


def test_prev_section_rolls_back_to_section_starts():
    orch = Orchestrator(_sectioned_doc(), None)
    # Mid-"1 Introduction" (cues 4..6): roll back to the section's own start.
    orch.pos = 5
    assert orch.prev_section() == 4
    # At the section start: roll back to the *previous* section's start.
    orch.pos = 4
    assert orch.prev_section() == 3            # "Abstract"
    # At the very start: roll back is a no-op.
    orch.pos = 0
    assert orch.prev_section() == 0


def test_section_jump_spokes_the_lead_in():
    """On a jump into a numbered section, the first spoken cue leads with its
    name ("Section 1, Introduction") so the listener knows where they landed."""
    orch = Orchestrator(_sectioned_doc(), None)
    orch.pos = 3
    assert orch.next_section() == 4            # into "1 Introduction", announce set
    ev, _audio = asyncio.run(orch.emit_next())
    assert ev.text.startswith("Section 1, Introduction"), ev.text
    # The flag is consumed: the next cue is spoken plainly.
    assert orch._announce_next is False
    ev2, _ = asyncio.run(orch.emit_next())
    assert "Section" not in ev2.text.split(" ")[0:1] or not ev2.text.startswith("Section 1")


def test_jump_does_not_speak_lead_in_for_non_announced_kind():
    """Front-matter kinds (title/abstract/keywords) are NOT announced on a jump —
    they either open the paper or name themselves in their own text."""
    orch = Orchestrator(_sectioned_doc(), None)
    # Jump from title to "authors" — authors is not an announced kind.
    assert orch.next_section() == 1
    assert orch._announce_next is False


def test_body_font_size_ignores_headings():
    """The body font size is the char-weighted mode, so a few large heading
    lines don't skew it (headings are measured *relative* to the body)."""
    blocks = [
        B("1 Introduction", 0, 50, 100, fs=12),       # large, few chars
        B("2 Method", 1, 50, 100, fs=12),
        B("A body sentence with a fair amount of words in it to count here.", 0, 50, 120, fs=9),
        B("Another body sentence with even more words to carry the mode along now.", 0, 50, 130, fs=9),
    ]
    assert _body_font_size(blocks) == 9.0


# --------------------------------------------------------------------------- #
# Fine per-cue navigation: next_cue / prev_cue move exactly one cue
# --------------------------------------------------------------------------- #
def test_next_cue_advances_by_one():
    """A fine forward step is +1 cue (a ~paragraph), not a section snap."""
    orch = Orchestrator(_sectioned_doc(), None)
    orch.pos = 5                       # mid-"1 Introduction" (cues 4..6)
    assert orch.next_cue() == 6
    assert orch._announce_next is False        # 6 is not a section start


def test_prev_cue_rolls_back_by_one_not_to_section_start():
    """The fine rollback goes back ONE cue, not to the current section's start.
    At cue 6 (mid "1 Introduction", 4..6) the coarse prev_section snaps to 4,
    whereas the fine prev_cue goes to 5."""
    coarse = Orchestrator(_sectioned_doc(), None)
    coarse.pos = 6
    assert coarse.prev_section() == 4            # coarse: section start

    fine = Orchestrator(_sectioned_doc(), None)
    fine.pos = 6
    assert fine.prev_cue() == 5                 # fine: one cue back, still mid-section
    assert fine._announce_next is False         # 5 is not a section start


def test_fine_jump_landing_on_section_start_announces():
    """A fine jump that happens to land on a section start still flags its name
    for the spoken lead-in (same path as the coarse jump)."""
    orch = Orchestrator(_sectioned_doc(), None)
    # "3 Results" starts at cue 9; from cue 8 a +1 step lands exactly on it.
    orch.pos = 8
    assert orch.next_cue() == 9
    assert orch._announce_next is True
    ev, _ = asyncio.run(orch.emit_next())
    assert ev.text.startswith("Section 3, Results"), ev.text


def test_fine_jumps_clamp_at_bounds():
    orch = Orchestrator(_sectioned_doc(), None)
    n = orch.n
    orch.pos = 0
    assert orch.prev_cue() == 0                # can't roll back past the start
    assert orch._announce_next is False        # the title is not an announced kind
    orch.pos = n
    assert orch.next_cue() == n                # can't advance past the end
