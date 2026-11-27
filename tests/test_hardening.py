"""M6 hardening — error / degradation paths across ingest / references / timeline.

The reader's one rule (the "Null*" idiom in ``server/app.py`` and the
``NullLlmClient`` / ``NullOcrEngine`` / ``NullPlayer`` fallbacks): a subsystem
failure must **never abort the read** — it degrades and logs, *or* it surfaces a
clean **typed error** at an input-validation boundary. This file pins that
contract for the three layers named in the M6 checkpoint. Two flavours of
assertion:

  * **degrade, never crash** — the pure helpers (``find_mentions``,
    ``build_index``, ``build_explain_schedule``, ``order_lines``) and the ingest
    per-page paths swallow a bad page / a bad input and yield *nothing* (an empty
    result), so one malformed page or one odd argument can't sink the document;
  * **clean typed errors** — ``pdf_source`` is the *input-validation boundary*:
    a missing / not-a-file / not-a-PDF path raises a clear
    ``FileNotFoundError`` / ``ValueError`` (not a cryptic PyMuPDF traceback),
    which is what the CLI shows the user.

It is hermetic: synthetic :class:`Block` / :class:`Cue` objects and a tiny
PyMuPDF-generated PDF — no ``test_pdfs/`` sample, no network, no TTS/OCR engine,
no live Ollama. So a bare ``[test]`` install runs it green.
"""

from __future__ import annotations

import pytest

from ownvoicereader.ingest import open_pdf, build_ocr_engine
from ownvoicereader.ingest.figure_table import render_region
from ownvoicereader.ingest.ocr import NullOcrEngine, _make_engine
from ownvoicereader.ingest.pdf_source import PdfSource
from ownvoicereader.ingest.reading_order import order_lines, _detect_columns
from ownvoicereader.ingest.text_extractor import (
    Line,
    _iter_lines,
    flag_sparse_pages,
    page_char_counts,
)
from ownvoicereader.model import Block, BBox, BlockKind, ReadingDocument
from ownvoicereader.reader.orchestrator import Cue
from ownvoicereader.reader.references import (
    ReferenceIndex,
    build_index,
    caption_target,
    find_mentions,
)
from ownvoicereader.reader.timeline import (
    ExplainItem,
    _context_of,
    build_explain_schedule,
    compose_explanation,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def B(text, page=0, bid=None):
    """A synthetic body-text block; id set explicitly so it can match a cue."""
    b = Block.text_block(text, page)
    if bid is not None:
        b.id = bid
    return b


class _FailingPage:
    """A PyMuPDF-stand-in page whose text parse blows up (a corrupt / malformed
    page). ``_iter_lines`` must swallow it and yield no lines, not propagate."""

    def get_text(self, *a, **k):
        raise RuntimeError("unparseable page")


class _FakeSource:
    """A minimal stand-in for an open :class:`PdfSource` whose ``doc`` supports
    page indexing but raises on an out-of-range page — enough to exercise
    ``render_region``'s degrade-to-``None`` path."""

    def __init__(self, doc):
        self._doc = doc

    @property
    def doc(self):
        return self._doc


# --------------------------------------------------------------------------- #
# A. pdf_source — the input-validation boundary (clean typed errors, not a
#    cryptic PyMuPDF traceback). These are the errors the CLI surfaces.
# --------------------------------------------------------------------------- #
def test_from_path_rejects_a_directory(tmp_path):
    # A path that exists but is *not a file* (a directory) is rejected with a
    # clean ValueError, before PyMuPDF ever sees it.
    d = tmp_path / "some_dir"
    d.mkdir()
    with pytest.raises(ValueError):
        PdfSource.from_path(d)


def test_open_pdf_rejects_a_non_pdf(tmp_path):
    # The convenience wrapper delegates to from_path, so a non-PDF file yields a
    # clean ValueError, not a raw parser traceback.
    bad = tmp_path / "not.pdf"
    bad.write_bytes(b"this is definitely not a pdf file, just some plain text.\n")
    with pytest.raises(ValueError):
        open_pdf(str(bad))


def test_from_path_missing_file_is_file_not_found(tmp_path):
    # Missing file -> FileNotFoundError (distinct from the ValueError of a bad one).
    with pytest.raises(FileNotFoundError):
        PdfSource.from_path(tmp_path / "nope.pdf")


# --------------------------------------------------------------------------- #
# B. text_extractor — a malformed page must not abort extraction
# --------------------------------------------------------------------------- #
def test_iter_lines_swallows_a_failing_page():
    # A page whose text layer can't be parsed yields no lines (logged, not raised),
    # so the rest of the document still reads.
    assert _iter_lines(_FailingPage(), 7) == []


def test_flag_sparse_pages_empty_and_all():
    # No lines at all: page_count 0 -> nothing sparse; a page_count with no lines
    # marks *every* page sparse (all need OCR / are blank).
    assert flag_sparse_pages([], 0) == []
    assert flag_sparse_pages([], 3) == [0, 1, 2]


def test_page_char_counts_empty():
    assert page_char_counts([]) == {}


def test_extract_lines_reuses_a_provided_source(tmp_path):
    # When a caller hands in an open source, we extract without closing it (the
    # caller owns the handle). A tiny generated PDF keeps this hermetic.
    import pymupdf

    from ownvoicereader.ingest.text_extractor import extract_lines

    pdf = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 72), "hello world, a line of text here", fontsize=12)
    doc.save(str(pdf))
    src = PdfSource.from_path(pdf)  # open ourselves; we own it
    rd = ReadingDocument(source_path=str(pdf), page_count=1)
    lines = extract_lines(rd, source=src)
    assert any("hello world" in ln.text for ln in lines)
    # We did not hand the source back closed: the caller still owns it, so the
    # live handle is still usable one more time.
    assert src.doc is not None
    src.close()


# --------------------------------------------------------------------------- #
# C. reading_order — empty / None / odd geometry all degrade, never crash
# --------------------------------------------------------------------------- #
def test_order_lines_empty_is_empty():
    assert order_lines([], [(612.0, 792.0)], 1) == []


def test_order_lines_none_lines_is_graceful():
    # A missing/None line list yields no blocks (pins the `lines = lines or []`
    # guard) instead of a TypeError on iteration.
    assert order_lines(None, [(612.0, 792.0)], 1) == []


def test_detect_columns_zero_width_single_column():
    # A zero (or negative) page width can't be projected, so it degrades to a
    # single full-width column rather than dividing by zero.
    assert _detect_columns([], 0.0, 792.0) == [(0.0, 1.0)]


def test_order_lines_page_beyond_geometry_still_orders():
    # A page with no geometry entry falls back to a single column (0,0 -> the
    # [(0,1)] slot), so the page's lines are still read in order.
    lines = [Line(page=1, text="first", x0=50, y0=100, x1=250, y1=111),
             Line(page=1, text="second", x0=50, y0=120, x1=250, y1=131)]
    blocks = order_lines(lines, page_geometry=[(612.0, 792.0)], page_count=2)
    assert len(blocks) >= 1
    assert "first" in blocks[0].text and "second" in blocks[0].text


# --------------------------------------------------------------------------- #
# D. references — the pure-text helpers degrade on odd / missing input
# --------------------------------------------------------------------------- #
def test_find_mentions_empty_and_whitespace():
    assert find_mentions("") == []
    assert find_mentions("   \n\t ") == []


def test_find_mentions_none_is_graceful():
    # A non-string input yields no references instead of crashing on finditer.
    assert find_mentions(None) == []


def test_find_mentions_non_str_is_graceful():
    assert find_mentions(12345) == []


def test_build_index_empty():
    idx = build_index([])
    assert isinstance(idx, ReferenceIndex)
    assert len(idx) == 0
    assert idx.ordered() == []
    assert idx.get("fig:1") is None


def test_build_index_none_is_graceful():
    # A missing/None block list yields an empty index (pins the `blocks or []`
    # guard) instead of a TypeError on iteration.
    idx = build_index(None)
    assert len(idx) == 0
    assert idx.ordered() == []


def test_build_index_ignores_non_text_blocks():
    # build_index scans only TEXT blocks; a list of figure/table blocks yields an
    # empty index (the reference grammar never looks at non-text blocks).
    blocks = [
        Block.figure_block(image=b"png", page=0, bbox=BBox(0, 0, 50, 50)),
        Block.table_block(table_grid=[["a", "b"], ["1", "2"]], page=0),
    ]
    assert build_index(blocks).entries == {}


def test_caption_target_empty_text_is_none():
    # A block with no text (or only whitespace) is not a caption.
    assert caption_target(B("")) is None
    assert caption_target(B("    ")) is None


# --------------------------------------------------------------------------- #
# E. timeline — the "right-moment" engine degrades on empty / missing input and
#    never loses an explanation to a chunk boundary
# --------------------------------------------------------------------------- #
def test_schedule_empty_inputs_is_empty():
    assert build_explain_schedule([], [], build_index([])) == {}


def test_schedule_none_index_is_graceful():
    # A missing/None reference index yields an empty schedule (pins the guard),
    # never an AttributeError on `.ordered()`.
    cues = [Cue(index=0, text="See Table 1 here.", block_id="b", page=0)]
    assert build_explain_schedule(cues, [B("See Table 1 here.", bid="b")], None) == {}


def test_schedule_empty_cues_is_graceful():
    blocks = [B("We report the results in Table 1 below.")]
    idx = build_index(blocks)
    assert build_explain_schedule([], blocks, idx) == {}


def test_compose_whitespace_caption_falls_back_to_bare_label():
    # A whitespace-only caption collapses to empty, so the spoken base is the bare
    # "is referenced here." (no dangling punctuation).
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1",
                       label_word="Figure", caption="   \n\t  ")
    item.text = compose_explanation(item)
    assert item.text == "Figure 1 is referenced here."


def test_context_of_empty_block_text():
    # A block that has the reference's id but no text yields an empty snippet.
    assert _context_of([B("", bid="b1")], "b1", "Table 1") == ""


def test_schedule_fires_on_block_opening_cue_when_ref_straddles_a_split():
    # A merged block that splits across two spoken cues can straddle the reference
    # itself ("…as in Table" | "1 below"): no *single* cue contains a complete
    # "Table 1", so the block-level grammar matches but the per-cue search finds
    # nothing. The fallback must fire on the block's *opening* cue so the
    # explanation is not lost to the chunk boundary.
    block = B("as in Table 1 below", page=0, bid="b1")
    cues = [
        Cue(index=0, text="as in Table", block_id="b1", page=0),
        Cue(index=1, text="1 below", block_id="b1", page=0),
    ]
    idx = build_index([block])
    assert idx.get("table:1") is not None  # the block-level grammar matched
    # And neither cue alone matches: the reference straddles the split.
    assert find_mentions("as in Table") == []
    assert find_mentions("1 below") == []
    sched = build_explain_schedule(cues, [block], idx)
    # The fallback fired on the opening cue (index 0), not the second one.
    assert 0 in sched
    assert [i.target for i in sched[0]] == ["table:1"]
    assert 1 not in sched


# --------------------------------------------------------------------------- #
# F. ocr — no engine available degrades to the Null engine (never raises)
# --------------------------------------------------------------------------- #
def test_make_engine_unknown_name_is_none():
    # An unrecognised engine name yields None (the caller falls through to the
    # next in the A4 order), never a crash.
    assert _make_engine("bogus-engine") is None
    assert _make_engine("") is None


def test_build_ocr_engine_no_engine_returns_null(monkeypatch):
    # When no engine can be built (the [ocr] extra absent), the factory returns
    # the graceful Null engine — available is False, ocr() yields nothing.
    import ownvoicereader.ingest.ocr as ocrmod

    monkeypatch.setattr(ocrmod, "_make_engine", lambda name: None)
    eng = build_ocr_engine()
    assert isinstance(eng, NullOcrEngine)
    assert eng.available is False
    assert eng.ocr(None) == []


# --------------------------------------------------------------------------- #
# G. figure_table — a render failure degrades to None (the caller skips the
#    media), not a crash
# --------------------------------------------------------------------------- #
def test_render_region_bad_page_returns_none():
    # An out-of-range page index can't be rasterised; render_region must return
    # None (so the figure/table media is simply skipped) rather than raise.
    src = _FakeSource(doc=[object()])  # only page 0 exists
    assert render_region(src, page=99, bbox=BBox(0, 0, 10, 10)) is None


def test_render_region_bad_bbox_returns_none():
    # A malformed region (a non-numeric coordinate) can't be clipped; degrade to
    # None rather than raise.
    src = _FakeSource(doc=[object()])
    assert render_region(src, page=0, bbox=("x", 0, "y", 0)) is None
