"""Tests for M2 checkpoint 4 — the "right-moment" engine (hermetic).

These pin the *timing* layer: when each figure/table is explained. The core
guarantee (A7) is that an ``EXPLAIN`` fires at the cue that actually **speaks the
first prose reference** — not merely the cue that opens the reference's block,
since a long merged block splits into several spoken chunks and the "…Table 1…"
clause can sit in a *later* chunk. The explanation is a *pending follow-on*: it is
emitted right after that cue without advancing the cue playhead, so section / cue
navigation is untouched.

The input is synthetic :class:`Block` / :class:`Cue` objects, so this is
deterministic and needs no PDF, network, or TTS engine.
"""

from __future__ import annotations

import asyncio

from ownvoicereader.model import Block, BlockKind, BBox, ReadingDocument, ReadingEventType
from ownvoicereader.reader.references import build_index
from ownvoicereader.reader.orchestrator import Cue, Orchestrator
from ownvoicereader.reader.timeline import (
    ExplainItem,
    compose_explanation,
    build_explain_schedule,
    _context_of,
    start_marker_text,
    end_marker_text,
)


def B(text, page=0, bid=None):
    """A synthetic body-text block; id set explicitly so it can match a cue."""
    b = Block.text_block(text, page)
    if bid is not None:
        b.id = bid
    return b


# --------------------------------------------------------------------------- #
# compose_explanation — the A2 "caption + context" base (LLM-free)
# --------------------------------------------------------------------------- #
def test_compose_with_caption_lowercases_leading_word():
    # A caption is a title-case noun phrase; lower-casing its first word makes
    # "Figure 3, stability …" read as a sentence, not a heading.
    item = ExplainItem(target="fig:3", kind=BlockKind.FIGURE, label="3",
                       label_word="Figure", caption="Stability analysis across queries.")
    item.text = compose_explanation(item)
    assert item.text == "Figure 3, stability analysis across queries."


def test_compose_caption_roman_label():
    item = ExplainItem(target="table:i", kind=BlockKind.TABLE, label="I",
                       label_word="Table", caption="Overview of methods.")
    item.text = compose_explanation(item)
    assert item.text == "Table I, overview of methods."


def test_compose_context_fallback_when_no_caption():
    item = ExplainItem(target="fig:2", kind=BlockKind.FIGURE, label="2",
                       label_word="Figure", context="Figure 2 illustrates the pipeline.")
    item.text = compose_explanation(item)
    assert item.text == "Figure 2 is referenced here: Figure 2 illustrates the pipeline."


def test_compose_neither_caption_nor_context():
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    item.text = compose_explanation(item)
    assert item.text == "Figure 1 is referenced here."


# --------------------------------------------------------------------------- #
# ExplainItem.meta — the viewer contract (kind + highlight target, no index)
# --------------------------------------------------------------------------- #
def test_explain_meta_shape():
    item = ExplainItem(target="fig:3", kind=BlockKind.FIGURE, label="3",
                       label_word="Figure", page=2, bbox=BBox(0, 0, 10, 10))
    m = item.meta
    assert m["kind"] == "explain"
    assert m["target"] == "fig:3"
    assert m["label"] == "3"
    assert m["label_word"] == "Figure"
    assert m["page"] == 2
    assert m["bbox"] == {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    # No bbox -> the key is absent (the viewer falls back to a page scroll).
    bare = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    assert "bbox" not in bare.meta
    # Crucially, no "index": an explanation is a pending follow-on, not a cue.
    assert "index" not in m


# --------------------------------------------------------------------------- #
# _context_of — the sentence that holds the reference (used when no caption)
# --------------------------------------------------------------------------- #
def test_context_of_extracts_surrounding_sentence():
    b = B("First sentence. See Table 1 for details here. Another sentence.", 0, "b1")
    ctx = _context_of([b], "b1", "Table 1")
    assert "Table 1 for details here" in ctx
    assert "First sentence" not in ctx
    assert "Another sentence" not in ctx


def test_context_of_falls_back_to_block_when_ref_absent():
    b = B("The results are interesting.", 0, "b1")
    assert _context_of([b], "b1", "Table 9") == "The results are interesting."


def test_context_of_empty_when_block_absent():
    assert _context_of([], "nope", "Table 1") == ""


# --------------------------------------------------------------------------- #
# build_explain_schedule — the timing: fire on the cue that holds the reference
# --------------------------------------------------------------------------- #
def test_schedule_fires_on_cue_containing_reference_not_block_start():
    # A merged block splits into two spoken cues; the "Table 1" clause is in the
    # *second* cue. The explanation must fire after that cue, not after the
    # block's opening cue.
    b1 = B("We compare the baselines and report the results in Table 1 here.", 0, "b1")
    b2 = B("Figure 2 illustrates the pipeline.", 1, "b2")
    cues = [
        Cue(index=0, text="We compare the baselines and", block_id="b1", page=0),
        Cue(index=1, text="report the results in Table 1 here.", block_id="b1", page=0),
        Cue(index=2, text="Figure 2 illustrates the pipeline.", block_id="b2", page=1),
    ]
    idx = build_index([b1, b2])
    sched = build_explain_schedule(cues, [b1, b2], idx)
    assert 0 not in sched                        # the reference is not in cue 0
    assert [i.target for i in sched[1]] == ["table:1"]
    assert [i.target for i in sched[2]] == ["fig:2"]
    # The scheduled items carry composed text and the reference's page/bbox.
    assert sched[1][0].text.startswith("Table 1")
    assert sched[2][0].page == 1


def test_schedule_skips_caption_only_target():
    # A figure that appears only via its caption (no prose reference) has no
    # "right moment" to fire on, so it is not scheduled.
    b0 = B("Intro paragraph.", 0, "b0")
    b1 = B("Figure 1: Illustration of the pipeline.", 0, "b1")
    b2 = B("The pipeline is described in detail.", 0, "b2")
    cues = [
        Cue(index=0, text="Intro paragraph.", block_id="b0", page=0),
        Cue(index=1, text="Figure 1: Illustration of the pipeline.", block_id="b1", page=0),
        Cue(index=2, text="The pipeline is described in detail.", block_id="b2", page=0),
    ]
    idx = build_index([b0, b1, b2])
    sched = build_explain_schedule(cues, [b0, b1, b2], idx)
    assert sched == {}


def test_schedule_skips_referred_block_without_cue():
    # The block carrying the reference was filtered out of the cue stream (e.g. a
    # running header), so there is no cue to fire on.
    b1 = B("See Table 1 for details.", 0, "b1")
    idx = build_index([b1])
    sched = build_explain_schedule([], [b1], idx)
    assert sched == {}


def test_schedule_multiple_targets_same_cue():
    # An interleaved two-column merge can put two references in one spoken cue;
    # both fire on that cue, in reading order.
    b1 = B("Results in Table 1 and Table 2 agree.", 0, "b1")
    cues = [Cue(index=0, text="Results in Table 1 and Table 2 agree.", block_id="b1", page=0)]
    idx = build_index([b1])
    sched = build_explain_schedule(cues, [b1], idx)
    assert [i.target for i in sched[0]] == ["table:1", "table:2"]


# --------------------------------------------------------------------------- #
# End-to-end: the orchestrator emits an EXPLAIN right after the reference cue
# --------------------------------------------------------------------------- #
def _drive(orch):
    async def go():
        events = []
        while True:
            r = await orch.emit_next()
            if r is None:
                break
            events.append(r[0])
        return events
    return asyncio.run(go())


def test_orchestrator_emits_explain_after_reference_cue():
    doc = ReadingDocument()
    doc.blocks = [
        B("We evaluate the model and report the results below.", 0),
        B("Table 1: Dataset statistics.", 0),                       # caption
        B("Table 1 shows the number of entities and relations.", 0),  # prose ref
    ]
    orch = Orchestrator(doc, None)  # null TTS -> events only, no audio
    events = _drive(orch)

    # The reference is on the *last* cue, so the group gets a start marker but no
    # end marker (there is no prose to return to).
    body = [e for e in events
            if e.type == ReadingEventType.EXPLAIN and e.meta.get("phase") == "body"]
    markers = [e for e in events
               if e.type == ReadingEventType.EXPLAIN and e.meta.get("phase") in ("start", "end")]
    assert len(body) == 1, [e.type.value for e in events]
    ex = body[0]
    assert ex.meta.get("kind") == "explain"
    assert ex.meta.get("target") == "table:1"
    assert ex.text.startswith("Table 1, dataset statistics")
    # A pending follow-on carries no "index" — the cue counter is not advanced.
    assert "index" not in ex.meta
    # The group is opened by a spoken start marker and has no closing marker here.
    assert len(markers) == 1 and markers[0].meta.get("phase") == "start"
    # The start marker precedes the body it opens.
    assert events.index(markers[0]) < events.index(ex)


def test_orchestrator_does_not_drop_last_cue_explanation():
    # The reference (and thus the explanation) is on the very last cue; the
    # loop must still emit the EXPLAIN after the text cues are exhausted.
    doc = ReadingDocument()
    doc.blocks = [
        B("Table 1: The final numbers.", 0),                      # caption (no ref)
        B("We report the results in Table 1 below.", 0),          # prose ref + last
    ]
    orch = Orchestrator(doc, None)
    events = _drive(orch)
    body = [e for e in events
            if e.type == ReadingEventType.EXPLAIN and e.meta.get("phase") == "body"]
    assert len(body) == 1, [e.type.value for e in events]
    assert body[0].meta.get("target") == "table:1"
    # The *last* spoken unit is the body itself (no closing marker at the very
    # end) — a last-cue explanation is never dropped.
    assert events[-1].type == ReadingEventType.EXPLAIN
    assert events[-1].meta.get("phase") == "body"


# --------------------------------------------------------------------------- #
# M2.5 markers + multi-cue explanations
# --------------------------------------------------------------------------- #
def test_start_marker_phrasing_is_singular_and_kind_aware():
    fig = [ExplainItem(target="fig:3", kind=BlockKind.FIGURE, label="3", label_word="Figure")]
    tab = [ExplainItem(target="table:2", kind=BlockKind.TABLE, label="2", label_word="Table")]
    assert start_marker_text(fig) == "Now, a closer look at this figure."
    assert start_marker_text(tab) == "Now, a closer look at this table."
    # Several of one kind pluralise.
    assert start_marker_text(fig + [fig[0]]) == "Now, a closer look at these figures."
    assert start_marker_text(tab + [tab[0]]) == "Now, a closer look at these tables."
    # A mixed group names both.
    assert start_marker_text(fig + tab) == "Now, a closer look at these figures and tables."


def test_end_marker_text():
    assert end_marker_text() == "Back to the text."


def test_orchestrator_splits_long_explanation_into_multiple_cues():
    # A long (vision) description is not crammed into one cue: it is split into
    # TTS-sized chunks, each its own EXPLAIN unit. The label leads only the first.
    doc = ReadingDocument()
    doc.blocks = [B("See Fig. 3 below.", 0)]
    orch = Orchestrator(doc, None)
    orch.explain_markers = False  # isolate the split from the spoken markers
    item = ExplainItem(
        target="fig:3", kind=BlockKind.FIGURE, label="3", label_word="Figure",
        vision=("a three-stage pipeline that first tokenizes the input and then routes it "
                "through a shared encoder before a lightweight decoder head produces the "
                "final scores; the three stages run in parallel and their outputs are "
                "fused by a small gating network before the prediction is returned"),
    )
    item.text = compose_explanation(item)
    orch._pending_explain.append(item)   # injected directly: isolates the split
    events = _drive(orch)
    body = [e for e in events
            if e.type == ReadingEventType.EXPLAIN and e.meta.get("phase") == "body"]
    assert len(body) >= 2, [e.text for e in body]
    # The figure label leads only the first body cue; later cues are continuations.
    assert body[0].text.startswith("Figure 3,")
    assert not body[1].text.startswith("Figure 3")
    # Injected directly, so no markers are emitted.
    assert all(e.meta.get("phase") not in ("start", "end")
               for e in events if e.type == ReadingEventType.EXPLAIN)


def test_orchestrator_brackets_multi_cue_explanation_with_one_pair():
    # A long body that splits into several cues still shares ONE start and ONE end
    # marker (not one pair per chunk), and the end marker is spoken when prose
    # follows.
    doc = ReadingDocument()
    doc.blocks = [
        B("We report the results in Table 1 here.", 0),
        B("The model is fast and accurate and reliable.", 0),
    ]
    orch = Orchestrator(doc, None)
    long_vision = ("the table lists each model alongside its accuracy across five benchmark "
                   "datasets and three random seeds and it also reports the training cost in "
                   "floating point operations per sample showing that the proposed method is "
                   "consistently the best while remaining the cheapest of all the baselines")
    for items in orch.explain_schedule.values():
        for it in items:
            it.vision = long_vision
            it.text = compose_explanation(it)
    events = _drive(orch)
    expl = [e for e in events if e.type == ReadingEventType.EXPLAIN]
    phases = [e.meta.get("phase") for e in expl]
    assert phases.count("start") == 1
    assert phases.count("end") == 1
    assert phases[0] == "start" and phases[-1] == "end"
    assert phases.count("body") >= 2
    assert any("Back to the text." in e.text for e in expl if e.meta.get("phase") == "end")


def test_orchestrator_no_end_marker_when_explanation_is_last():
    # A reference on the very last cue gets a start marker but no "back to the
    # text" — there is no prose to return to.
    doc = ReadingDocument()
    doc.blocks = [B("We report the results in Table 1 below.", 0)]
    orch = Orchestrator(doc, None)
    events = _drive(orch)
    expl = [e for e in events if e.type == ReadingEventType.EXPLAIN]
    phases = [e.meta.get("phase") for e in expl]
    assert "start" in phases
    assert "end" not in phases
    assert phases[-1] == "body"


def test_orchestrator_explain_markers_off_emits_body_only():
    doc = ReadingDocument()
    doc.blocks = [
        B("We report the results in Table 1 here.", 0),
        B("The model is fast and accurate.", 0),
    ]
    orch = Orchestrator(doc, None)
    orch.explain_markers = False
    events = _drive(orch)
    expl = [e for e in events if e.type == ReadingEventType.EXPLAIN]
    assert [e.meta.get("phase") for e in expl] == ["body"], [e.text for e in expl]


def test_orchestrator_body_meta_has_phase_and_no_index():
    doc = ReadingDocument()
    doc.blocks = [B("See Fig. 3 here.", 0), B("More text follows after.", 0)]
    orch = Orchestrator(doc, None)
    events = _drive(orch)
    body = [e for e in events
            if e.type == ReadingEventType.EXPLAIN and e.meta.get("phase") == "body"]
    assert body
    m = body[0].meta
    assert m["phase"] == "body"
    assert m["target"] == "fig:3"
    # The pending-follow-on invariant holds: an explanation never carries an index.
    assert "index" not in m
