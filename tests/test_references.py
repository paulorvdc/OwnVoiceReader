"""Tests for M2 checkpoint 1 — figure/table reference detection (hermetic).

These pin the pure-text logic that locates the "right moment" to explain a
figure or table:

  * ``find_mentions`` recognises every reference shape the samples use
    (Figure 3 / Fig. 3 / Fig. 3a / Table 2(b) / Tbl. 2 / TABLE I) *and* does not
    over-match ordinary prose ("Table in the results", "Fig. 3abc");
  * ``caption_target`` tells a caption ("Table 1: …", "TABLE I OVERVIEW",
    "Figure 1. …") apart from a sentence that merely *opens* with a reference
    ("Figure 6 displays …");
  * ``build_index`` produces one entry per figure/table, recording the caption,
    the reading-order position of the *first prose reference* (the EXPLAIN
    moment), and the total mention count.

The input is synthetic :class:`Block` objects, so this is deterministic and needs
no PDF, network, or TTS engine.
"""

from __future__ import annotations

from ownvoicereader.model import Block, BlockKind
from ownvoicereader.reader.references import (
    caption_target,
    find_mentions,
    build_index,
)


def B(text, page=0):
    """A synthetic body-text block (the only kind build_index scans in M2 C1)."""
    return Block.text_block(text, page)


# --------------------------------------------------------------------------- #
# find_mentions — the reference grammar
# --------------------------------------------------------------------------- #
def test_mentions_basic_shapes():
    # Every reference shape the sample PDFs use resolves to its canonical target.
    cases = [
        ("as shown in Fig. 3, the results improve", "fig:3"),
        ("Figure 6 displays the obtained results", "fig:6"),
        ("we compare against the baselines in Table 1", "table:1"),
        ("results in Fig. 3(a) confirm the trend", "fig:3a"),
        ("see TABLE I for an overview", "table:i"),
    ]
    for text, target in cases:
        refs = find_mentions(text)
        targets = [r.target for r in refs]
        assert target in targets, f"{text!r} -> {targets}"


def test_mentions_panel_sublabels():
    # "2(b)" parenthesised and "3a" glued both normalise to the same panel key.
    assert find_mentions("Table 2(b) reports nDCG")[0].target == "table:2b"
    assert find_mentions("Fig. 3a shows the left panel")[0].target == "fig:3a"
    got = [r.target for r in find_mentions("Fig. 4b and Fig. 4c agree")]
    assert got == ["fig:4b", "fig:4c"], got


def test_mentions_roman_numerals():
    # IEEE style: uppercase roman numerals I..XII are tables.
    assert find_mentions("Table II reports the worst case")[0].target == "table:ii"
    assert find_mentions("Table XII is the largest")[0].target == "table:xii"
    # The kind word "Table" with an *uppercase* roman must not be confused with a
    # following word that merely starts with a roman-ish letter.
    assert find_mentions("Table in the results should be high") == []
    assert find_mentions("Table learning rates matter") == []


def test_mentions_do_not_over_match():
    # A number glued to a following word is not a reference; a roman "i" that is
    # really the first letter of a word ("in") is not a number.
    assert find_mentions("Figure 3abc is broken") == []
    assert find_mentions("Table in the results") == []
    assert find_mentions("There is no figure here at all") == []


def test_mentions_multiple_in_one_block():
    refs = find_mentions("The Fig. 3 in Table 2 both agree with Fig. 5.")
    got = [r.target for r in refs]
    assert got == ["fig:3", "table:2", "fig:5"], got


def test_mentions_raw_is_the_matched_text():
    r = find_mentions("as in Fig. 3, see it")[0]
    assert r.raw == "Fig. 3"
    assert r.kind == BlockKind.FIGURE
    # page / block_id are left to the caller to stamp.
    assert r.page == -1 and r.block_id == ""


# --------------------------------------------------------------------------- #
# caption_target — caption vs a sentence that opens with a reference
# --------------------------------------------------------------------------- #
def test_caption_colon_separator():
    target, cap = caption_target(B("Table 1: Dataset statistics."))
    assert target == "table:1"
    assert cap == "Dataset statistics."


def test_caption_period_separator():
    # sbc style: "Figure 1. Illustration of the task."
    target, cap = caption_target(B("Figure 1. Illustration of the task."))
    assert target == "fig:1"
    assert cap.startswith("Illustration of the task")


def test_caption_title_case_roman():
    # IEEE style: "TABLE I OVERVIEW OF METHODS" — a title, not a sentence.
    target, cap = caption_target(B("TABLE I OVERVIEW OF METHODS"))
    assert target == "table:i"
    assert cap == "OVERVIEW OF METHODS"


def test_caption_rejects_prose_verb():
    # "Figure 6 displays …" opens with a reference but is prose, not a caption.
    assert caption_target(B("Figure 6 displays the obtained results.")) is None
    assert caption_target(B("Table 2(b) reports the nDCG@10 values.")) is None


def test_caption_rejects_bare_label():
    # A bare "Figure 3" line (no caption text after it) is not a caption.
    assert caption_target(B("Figure 3")) is None
    # And a label in the middle of an ordinary block is not a caption.
    assert caption_target(B("As discussed, Fig. 3 shows the trend clearly.")) is None


# --------------------------------------------------------------------------- #
# build_index — one entry per figure/table, first ref = the EXPLAIN moment
# --------------------------------------------------------------------------- #
def test_build_index_caption_and_prose_ref():
    blocks = [
        B("We evaluate the model and report the results below."),       # 0
        B("Table 1: Dataset statistics."),                               # 1  caption
        B("Table 1 shows the number of entities and relations."),        # 2  prose ref
        B("Further analysis follows."),                                   # 3
    ]
    idx = build_index(blocks)
    e = idx.get("table:1")
    assert e is not None
    assert e.kind == BlockKind.TABLE
    assert e.label == "1"
    assert e.caption == "Dataset statistics."
    # The first *prose* reference (block 2) is the EXPLAIN moment — not the
    # caption block (1) and not every mention.
    assert e.first_ref_pos == 2
    assert e.first_ref_raw == "Table 1"
    assert e.has_prose_ref is True
    # Caption (1) + prose mention (2) = 2 total mentions.
    assert e.mention_count == 2


def test_build_index_first_ref_wins_over_later_mentions():
    blocks = [
        B("Table 2 is discussed first here."),        # 0  first mention
        B("Table 2: Results of the experiment."),       # 1  caption (skipped in p2)
        B("See also Table 2 for the full numbers."),    # 2  later mention
    ]
    idx = build_index(blocks)
    e = idx.get("table:2")
    assert e is not None
    assert e.first_ref_pos == 0            # the *first* mention in reading order
    assert e.first_ref_raw == "Table 2"
    assert e.caption == "Results of the experiment."
    # Caption (block 1) + two prose mentions (blocks 0 and 2) = 3 total.
    assert e.mention_count == 3


def test_build_index_caption_only_figure():
    # A figure that appears only via its caption (no prose reference) still gets
    # an entry; it has no prose ref and sorts last.
    blocks = [
        B("Intro paragraph."),                                  # 0
        B("Figure 1: Illustration of the pipeline."),            # 1  caption
        B("The pipeline is described in detail."),              # 2
    ]
    idx = build_index(blocks)
    e = idx.get("fig:1")
    assert e is not None
    assert e.caption == "Illustration of the pipeline."
    assert e.has_prose_ref is False
    assert e.first_ref_pos == -1
    # A caption-only entry sorts after any entry that has a prose reference.
    order = [e.target for e in idx.ordered()]
    assert order == ["fig:1"]


def test_build_index_ordered_by_first_ref_position():
    blocks = [
        B("Figure 1: first caption."),                          # 0  caption
        B("Table 1: second caption."),                          # 1  caption
        B("Results in Fig. 1 confirm the baseline."),            # 2  -> fig:1 ref
        B("Table 1 lists the per-method scores."),              # 3  -> table:1 ref
    ]
    idx = build_index(blocks)
    order = [e.target for e in idx.ordered()]
    # fig:1 is first referenced at block 2, table:1 at block 3.
    assert order == ["fig:1", "table:1"], order


def test_build_index_distinct_panel_targets():
    # Panels are tracked as their own targets (3 vs 3a), not collapsed. Here the
    # parent "Figure 3" appears only as a caption (no prose "Figure 3"), so it is
    # caption-only, while its panels 3a/3b are prose-referenced at block 0.
    blocks = [
        B("Figure 3a and Figure 3b show two views."),            # 0  prose refs
        B("Figure 3: the combined view."),                       # 1  caption
    ]
    idx = build_index(blocks)
    assert idx.get("fig:3") is not None
    assert idx.get("fig:3a") is not None
    assert idx.get("fig:3b") is not None
    # Parent is caption-only (its caption block is skipped in the prose pass).
    assert idx.get("fig:3").has_prose_ref is False
    assert idx.get("fig:3").caption == "the combined view."
    # The panels are the prose-referenced targets, both first mentioned at block 0.
    assert idx.get("fig:3a").first_ref_pos == 0
    assert idx.get("fig:3b").first_ref_pos == 0
