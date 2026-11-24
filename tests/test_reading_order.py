"""Tests for M1 reading order + cue building (hermetic, no network / no sample).

These feed synthetic :class:`Line` / :class:`Block` objects directly so the
geometry + filtering logic is tested deterministically, plus one integration
test that generates a real 2-column PDF with PyMuPDF.
"""

from __future__ import annotations

import pymupdf

from ownvoicereader.ingest.reading_order import (
    order_lines,
    _detect_columns,
    _is_stray,
)
from ownvoicereader.ingest.text_extractor import Line, flag_sparse_pages
from ownvoicereader.model import Block, BBox, ReadingDocument, BlockKind
from ownvoicereader.reader.orchestrator import (
    build_cues,
    _is_page_number_or_stray,
    _split_into_chunks,
    _MAX_CHUNK_CHARS,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def L(page, text, x0, y0, x1=None, y1=None, fs=11.0):
    x1 = x1 if x1 is not None else x0 + 200
    y1 = y1 if y1 is not None else y0 + fs
    return Line(page=page, text=text, x0=x0, y0=y0, x1=x1, y1=y1, font_size=fs)


# --------------------------------------------------------------------------- #
# Column detection
# --------------------------------------------------------------------------- #
def test_single_column_no_gutter():
    lines = [L(0, "a", 50, 100), L(0, "b", 50, 120), L(0, "c", 50, 140)]
    slots = _detect_columns(lines, page_width=612, page_height=792)
    assert len(slots) == 1
    assert slots[0][0] == 0.0 and slots[0][1] == 612.0


def test_two_columns_detected():
    # Left column x~50..250, right column x~320..520, gutter ~250..320 empty.
    lines = []
    for y in (100, 120, 140, 160):
        lines.append(L(0, "left", 50, y, 250, y + 11))
        lines.append(L(0, "right", 320, y, 520, y + 11))
    slots = _detect_columns(lines, page_width=612, page_height=792)
    assert len(slots) == 2, slots
    # The first slot must be the left column, the second the right.
    assert slots[0][1] < 300
    assert slots[1][0] > 300


def test_full_width_line_does_not_open_a_false_gutter():
    # A single full-width line spanning the whole page should NOT be treated as
    # two columns, and its own x-extent must not hide the real gutter.
    lines = [
        L(0, "A full width title spanning the page", 40, 60, 580, 80),  # spans across
    ]
    for y in (100, 120, 140):
        lines.append(L(0, "left column body line here", 50, y, 250, y + 11))
        lines.append(L(0, "right column body line here", 320, y, 520, y + 11))
    slots = _detect_columns(lines, page_width=612, page_height=792)
    # The full-width line is excluded from the projection, so the body gutter
    # is still found => 2 columns.
    assert len(slots) == 2, slots


# --------------------------------------------------------------------------- #
# Reading order
# --------------------------------------------------------------------------- #
def test_two_column_reading_order():
    lines = []
    # Left column: A1 (y100), A2 (y120); right column: B1 (y100), B2 (y120)
    lines.append(L(0, "alpha first line here", 50, 100, 250, 111))
    lines.append(L(0, "alpha second line here", 50, 120, 250, 131))
    lines.append(L(0, "beta first line here", 320, 100, 520, 111))
    lines.append(L(0, "beta second line here", 320, 120, 520, 131))
    blocks = order_lines(lines, page_geometry=[(612, 792)], page_count=1)
    text = " ".join(b.text for b in blocks)
    # Left column must be fully read before the right column.
    assert text.index("alpha first") < text.index("alpha second") < \
        text.index("beta first") < text.index("beta second"), text


def test_pages_read_in_order():
    lines = [
        L(0, "p0-left", 50, 100, 250, 111),
        L(1, "p1-left", 50, 100, 250, 111),
        L(0, "p0-right", 320, 100, 520, 111),
    ]
    blocks = order_lines(lines, page_geometry=[(612, 792), (612, 792)], page_count=2)
    text = " ".join(b.text for b in blocks)
    assert text.index("p0-left") < text.index("p0-right") < text.index("p1-left"), text


# --------------------------------------------------------------------------- #
# Sparse-page detection
# --------------------------------------------------------------------------- #
def test_sparse_page_flagged():
    lines = [
        L(0, "a long enough line of text here to count", 50, 100),  # > 30 chars
        L(1, "hi", 50, 100),  # tiny
    ]
    sparse = flag_sparse_pages(lines, page_count=2, min_chars=30)
    assert 1 in sparse and 0 not in sparse, sparse


# --------------------------------------------------------------------------- #
# Stray / page-number filtering
# --------------------------------------------------------------------------- #
def test_stray_detection():
    assert _is_stray("9024") is True
    assert _is_stray(" 12 ") is True
    assert _is_stray("3.1") is True
    assert _is_stray("Introduction") is False
    assert _is_stray("E‡") is True  # footnote marker
    assert _is_page_number_or_stray("9025") is True
    assert _is_page_number_or_stray("This is a sentence.") is False


# --------------------------------------------------------------------------- #
# Cue building (integration over the Block contract)
# --------------------------------------------------------------------------- #
def _doc_with(blocks):
    d = ReadingDocument()
    d.blocks = blocks
    return d


def test_build_cues_drops_page_numbers_and_dups():
    blocks = [
        Block.text_block("Hello world this is the first real sentence here.", 0,
                         BBox(50, 100, 250, 111)),
        Block.text_block("9024", 0, BBox(300, 750, 314, 760)),  # page number
        Block.text_block("CIKM 2026, Rome, Italy", 0, BBox(53, 61, 558, 70)),  # header
        Block.text_block("CIKM 2026, Rome, Italy", 1, BBox(53, 61, 558, 70)),  # dup header
        Block.text_block("Second real paragraph with more words in it indeed.", 1,
                         BBox(50, 100, 250, 111)),
    ]
    cues = build_cues(_doc_with(blocks))
    texts = [c.text for c in cues]
    joined = " ".join(texts)
    assert "9024" not in joined  # page number dropped
    # The duplicate header appears at most once.
    assert joined.count("CIKM 2026") == 1
    assert len(cues) >= 2  # the two real paragraphs survive


def test_generated_two_column_pdf_orders_columns():
    """End-to-end: a real 2-column PDF reads left column before right."""
    import tempfile
    from ownvoicereader.ingest import open_pdf, build_document

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # Left column (x 50..250), then right column (x 320..520).
    page.insert_text((50, 150), "LEFTCOL-A first line", fontsize=12)
    page.insert_text((50, 180), "LEFTCOL-B second line", fontsize=12)
    page.insert_text((320, 150), "RIGHTCOL-A first line", fontsize=12)
    page.insert_text((320, 180), "RIGHTCOL-B second line", fontsize=12)
    doc.save(tmp.name)
    doc.close()

    rd = open_pdf(tmp.name)
    build_document(rd)
    text = " ".join(b.text for b in rd.blocks)
    # Both columns present, and left column reads before right column.
    assert "LEFTCOL-A" in text and "RIGHTCOL-A" in text
    assert text.index("LEFTCOL-A") < text.index("LEFTCOL-B") < text.index("RIGHTCOL-A"), text


# --------------------------------------------------------------------------- #
# Chunker — the "no sentence is ever dropped" invariant (M1.5)
#
# A prior version of _split_into_chunks silently dropped a whole sentence
# whenever a sentence didn't fit the rest of the running chunk and was itself
# under the max — which made prose vanish mid-paragraph during playback. These
# tests pin the invariant so it can't regress.
# --------------------------------------------------------------------------- #
def test_split_into_chunks_drops_no_sentence():
    # A body with short, medium, and long (> max) sentences, plus line-broken
    # hyphenation like a real PDF ("pre- diction").
    text = (
        "First sentence is short. "
        "This one is a little bit longer but still under the chunk character "
        "limit that the synthesizer prefers so it stays a natural phrase. "
        "Embedding models constitute the main link pre- diction approach to "
        "complete knowledge graphs and they have become especially valuable for "
        "recommender systems, web search, and a great many other downstream "
        "applications across academia and industry in recent years. "
        "x " * 40 + "x. "  # a single over-long "sentence" that must word-break
        "We find that high-performance models produce divergent predictions. "
        "Short. Another short one. And yet another."
    )
    chunks = _split_into_chunks(text)
    # 1) No word is lost: the multiset of whitespace tokens is identical.
    assert " ".join(chunks).split() == text.split()
    # 2) No chunk exceeds the cap (over-long sentences are word-broken).
    assert all(len(c) <= _MAX_CHUNK_CHARS for c in chunks)
    # 3) No empty chunks.
    assert all(c.strip() for c in chunks)


def test_split_into_chunks_springer_front_matter_regression():
    """The exact front-matter block that used to lose ~40% of its words."""
    block = (
        "Link Prediction or Perdition: the Seeds of Instability in Knowledge "
        "Graph Embeddings Guillaume Meroue[0009-0004-7111-3202], Fabien Gandon, "
        "and Pierre Monnin. Universite Cote d'Azur, Inria, CNRS, I3S, France "
        "firstname.lastname@inria.fr arXiv:2606.03365v1 [cs.LG] 2 Jun 2026 "
        "Abstract. Embedding models (KGEMs) constitute the main link pre- "
        "diction approach to complete knowledge graphs. Standard evaluation "
        "protocols emphasize rank-based metrics such as MRR or Hits@K, but "
        "usually overlook the influence of random seeds on result stability. "
        "Moreover, these metrics conceal potential instabilities in individual "
        "predictions and in the organization of embedding spaces. In this work, "
        "we conduct a systematic stability analysis of multiple KGEMs across "
        "several datasets. We find that high-performance models actually produce "
        "divergent predictions at the triple level and highly variable embedding "
        "spaces. By isolating stochastic factors, we show that each independently "
        "induces instability of comparable magnitude. Furthermore, for a given "
        "model, hy- perparameter configurations with better MRR are not "
        "guaranteed to be more stable. Moreover, voting, albeit a known "
        "remediation mechanism, only provides a limited enhancement of stability."
    )
    chunks = _split_into_chunks(block)
    assert " ".join(chunks).split() == block.split()  # 0 words lost
    # The specific sentences that used to vanish are all present.
    joined = " ".join(chunks)
    for probe in ("Embedding models (KGEMs) constitute the main link pre-",
                  "conceal potential instabilities in individual predictions",
                  "high-performance models actually produce divergent",
                  "hy- perparameter configurations with better MRR"):
        assert probe in joined, probe


def test_split_into_chunks_is_idempotent_on_rejoin():
    """Re-chunking the concatenation never changes the word multiset."""
    text = ("Alpha beta gamma delta epsilon. " * 30)
    once = _split_into_chunks(text)
    twice = _split_into_chunks(" ".join(once))
    assert " ".join(once).split() == " ".join(twice).split() == text.split()
