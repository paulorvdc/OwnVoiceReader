"""Tests for M2.5 — the vision explainer.

Hermetic coverage (no server, no PDF, no TTS) of the pieces that don't need a real
model:

  * ``link_media`` pairs each figure/table *block* with its caption's target by
    same-page proximity, and leaves cross-page floats unlinked (a graceful A2
    fallback).
  * ``_grid_text`` renders a table's cells compactly, dropping empties + bounding.
  * ``compose_explanation`` prefers a vision description over the A2 base and
    tidies a redundant self-reference ("this figure shows …").
  * :class:`Explainer.describe` / ``precompute`` fill ``item.vision`` from a fake
    client, degrade to "" when the client is unavailable, and render a table
    region on demand.

One integration test does a real vision call against a live Ollama server, skipped
automatically when none is reachable.
"""

from __future__ import annotations

import socket

import pytest

from ownvoicereader.model import BBox, Block, BlockKind
from ownvoicereader.reader.timeline import ExplainItem, compose_explanation, _clean_vision
from ownvoicereader.llm.client import LlmClient, NullLlmClient
from ownvoicereader.llm.explainer import Explainer, link_media, _grid_text


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fig(image: bytes, page: int, bbox=BBox(0, 100, 200, 300)):
    return Block.figure_block(image, page, bbox)


def _table(grid, page: int, bbox=BBox(0, 400, 300, 500)):
    return Block.table_block(grid, page, bbox)


class FakeClient(LlmClient):
    """A deterministic client: returns a canned answer, records the last call."""

    name = "fake"

    def __init__(self, response: str = "a pipeline of three stages"):
        self._response = response
        self.calls = 0
        self.last_messages = None
        self.last_image = None

    @property
    def available(self) -> bool:
        return True

    def _chat_sync(self, model, messages, stream, options):
        self.calls += 1
        self.last_messages = list(messages)
        if messages:
            self.last_image = (messages[0].get("images") or [None])[0]
        return self._response


class _FakePage:
    def get_pixmap(self, clip, dpi=150):
        class _Pix:
            width, height = 10, 10

            def tobytes(self, _fmt):
                return b"\x89PNG-fake-region"

        return _Pix()


class _FakeSource:
    """A stand-in open source whose pages render any region to a fake PNG."""

    def __init__(self):
        self.doc = self

    def __getitem__(self, _i):
        return _FakePage()

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# link_media — target -> media block by same-page caption proximity
# --------------------------------------------------------------------------- #
def test_link_media_pairs_figure_with_its_caption():
    cap = Block.text_block("Figure 1: Illustration of the pipeline.", 0, BBox(0, 500, 200, 520))
    fig = _fig(b"png", page=0, bbox=BBox(0, 300, 200, 490))  # just above the caption
    blocks = [cap, fig]
    media = link_media(blocks)
    assert media.get("fig:1") is fig


def test_link_media_leaves_cross_page_float_unlinked():
    # Caption on page 0, figure block on page 1 -> no same-page match -> unlinked.
    cap = Block.text_block("Figure 1: The pipeline.", 0, BBox(0, 500, 200, 520))
    fig = _fig(b"png", page=1, bbox=BBox(0, 300, 200, 490))
    blocks = [cap, fig]
    media = link_media(blocks)
    assert "fig:1" not in media


def test_link_media_keeps_first_media_per_target():
    cap = Block.text_block("Table 1: Results.", 0, BBox(0, 600, 300, 620))
    t1 = _table([["a", "b"], ["1", "2"]], page=0, bbox=BBox(0, 500, 300, 590))
    t2 = _table([["c"]], page=0, bbox=BBox(0, 200, 300, 300))  # same page, farther
    blocks = [cap, t1, t2]
    media = link_media(blocks)
    assert media.get("table:1") is t1  # nearest caption wins; first kept


# --------------------------------------------------------------------------- #
# _grid_text
# --------------------------------------------------------------------------- #
def test_grid_text_drops_empties_and_joins():
    grid = [["Model", "Score"], ["A", "0.9"], ["B", ""]]
    text = _grid_text(grid)
    assert "Model | Score" in text
    assert "A | 0.9" in text
    assert "B |" not in text  # the empty cell was dropped


def test_grid_text_is_length_bounded():
    grid = [[str(i)] for i in range(1000)]
    text = _grid_text(grid, max_chars=50)
    assert len(text) <= 51  # 50 + the "…" ellipsis
    assert text.endswith("…")


# --------------------------------------------------------------------------- #
# compose_explanation with vision (M2.5)
# --------------------------------------------------------------------------- #
def test_compose_prefers_vision_over_caption():
    item = ExplainItem(target="fig:3", kind=BlockKind.FIGURE, label="3",
                       label_word="Figure",
                       caption="Stability analysis.",
                       vision="a line chart showing three runs stay close together.")
    item.text = compose_explanation(item)
    assert item.text == "Figure 3, a line chart showing three runs stay close together."
    assert "Stability analysis" not in item.text  # the base caption is superseded


def test_compose_vision_falls_back_to_base_when_absent():
    item = ExplainItem(target="fig:3", kind=BlockKind.FIGURE, label="3",
                       label_word="Figure", caption="Stability analysis.")
    item.text = compose_explanation(item)
    assert item.text == "Figure 3, stability analysis."


def test_compose_vision_cleans_self_reference():
    # A model that answers "This figure shows a pipeline" reads awkwardly after
    # our own "Figure 1," — the lead-in is stripped.
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1",
                       label_word="Figure", vision="This figure shows a pipeline.")
    assert item.vision and "pipeline" in compose_explanation(item)
    assert not compose_explanation(item).lower().startswith("figure 1, this figure")


def test_compose_vision_cleans_image_and_it_shows():
    # "This image shows …" and "It shows …" are stripped the same way.
    img = ExplainItem(target="fig:5", kind=BlockKind.FIGURE, label="5",
                      label_word="Figure", vision="This image shows a network graph.")
    it = ExplainItem(target="fig:6", kind=BlockKind.FIGURE, label="6",
                     label_word="Figure", vision="It shows a prompt template.")
    assert compose_explanation(img) == "Figure 5, a network graph."
    assert compose_explanation(it) == "Figure 6, a prompt template."


def test_compose_vision_does_not_overstrip_body():
    # A phrase that is not a self-reference lead-in is kept whole.
    item = ExplainItem(target="table:2", kind=BlockKind.TABLE, label="2",
                       label_word="Table",
                       vision="The performance of four approaches across datasets.")
    out = compose_explanation(item)
    assert "performance of four approaches" in out


def test_clean_vision_lowercases_first_word():
    assert _clean_vision("A pipeline of three stages").startswith("a pipeline")


def test_meta_reports_vision_flag():
    plain = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    assert plain.meta["vision"] is False
    vis = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1",
                      label_word="Figure", vision="a chart.")
    assert vis.meta["vision"] is True


# --------------------------------------------------------------------------- #
# Explainer.describe / precompute with a fake client
# --------------------------------------------------------------------------- #
def test_describe_figure_uses_image_and_composes():
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    fig = _fig(b"png-bytes", page=0)
    client = FakeClient(response="a three-stage pipeline.")
    out = Explainer(client).describe(item, fig)
    assert out == "a three-stage pipeline."
    assert client.calls == 1
    assert client.last_image == b"png-bytes"  # the figure image was sent


def test_describe_unavailable_client_returns_empty():
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    out = Explainer(NullLlmClient()).describe(item, _fig(b"png", 0))
    assert out == ""  # vision off -> the A2 base is kept


def test_describe_table_renders_region_and_sends_grid():
    item = ExplainItem(target="table:1", kind=BlockKind.TABLE, label="1", label_word="Table")
    table = _table([["Model", "Score"], ["A", "0.9"]], page=0)
    client = FakeClient(response="the A model scores highest.")
    out = Explainer(client).describe(item, table, source=_FakeSource())
    assert out == "the A model scores highest."
    # The table region was rendered and cached on the block...
    assert table.image == b"\x89PNG-fake-region"
    # ...and the grid text was part of the prompt sent to the model.
    prompt = client.last_messages[0]["content"]
    assert "Model | Score" in prompt
    assert client.last_image == b"\x89PNG-fake-region"


def test_describe_table_with_grid_only_when_no_source():
    # No source to render: fall back to the grid text alone (no image).
    item = ExplainItem(target="table:1", kind=BlockKind.TABLE, label="1", label_word="Table")
    table = _table([["Model", "Score"], ["A", "0.9"]], page=0)
    client = FakeClient(response="A scores highest.")
    out = Explainer(client).describe(item, table)  # source=None
    assert out == "A scores highest."
    prompt = client.last_messages[0]["content"]
    assert "Model | Score" in prompt
    assert client.last_image is None  # no image available


def test_precompute_fills_items_with_media_and_counts():
    item_a = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    item_b = ExplainItem(target="fig:2", kind=BlockKind.FIGURE, label="2", label_word="Figure")
    fig1 = _fig(b"p1", page=0)
    fig2 = _fig(b"p2", page=0)
    media = {"fig:1": fig1, "fig:2": fig2}
    schedule = {0: [item_a], 1: [item_b]}
    client = FakeClient(response="a chart.")
    n = Explainer(client).precompute(schedule, media, doc=None, source=_FakeSource())
    assert n == 2
    assert item_a.vision == "a chart." and item_b.vision == "a chart."
    # The composed text now leads with the vision phrase.
    assert item_a.text.startswith("Figure 1, a chart.")
    assert client.calls == 2


def test_precompute_skips_items_without_media():
    item = ExplainItem(target="fig:9", kind=BlockKind.FIGURE, label="9", label_word="Figure")
    schedule = {0: [item]}
    client = FakeClient()
    n = Explainer(client).precompute(schedule, {}, doc=None, source=_FakeSource())
    assert n == 0 and client.calls == 0 and item.vision == ""


def test_precompute_graceful_when_client_unavailable():
    item = ExplainItem(target="fig:1", kind=BlockKind.FIGURE, label="1", label_word="Figure")
    media = {"fig:1": _fig(b"png", 0)}
    schedule = {0: [item]}
    n = Explainer(NullLlmClient()).precompute(schedule, media, doc=None, source=_FakeSource())
    assert n == 0 and item.vision == ""


# --------------------------------------------------------------------------- #
# Integration — a real vision call (skipped when no Ollama is reachable)
# --------------------------------------------------------------------------- #
def _server_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1.0):
            return True
    except OSError:
        return False


_live = _server_reachable()


@pytest.mark.skipif(not _live, reason="no Ollama server on 11434")
def test_integration_vision_describes_a_figure():
    import pathlib
    from ownvoicereader.ingest import open_pdf, build_document
    from ownvoicereader.llm import build_llm_client, Explainer, link_media
    from ownvoicereader.reader.references import build_index
    from ownvoicereader.reader.timeline import build_explain_schedule

    pdf = pathlib.Path(__file__).resolve().parent.parent / "test_pdfs" / "1-column_sbc.pdf"
    doc = open_pdf(str(pdf))
    build_document(doc)
    index = build_index(doc.blocks)
    media = link_media(doc.blocks)
    # Find a captioned figure that is linked to a media block.
    target = next((t for t, b in media.items() if b.kind is BlockKind.FIGURE), None)
    if target is None:
        pytest.skip("no linked figure in this sample")
    block = media[target]
    item = ExplainItem(target=target, kind=BlockKind.FIGURE, label=target.split(":", 1)[1],
                       label_word="Figure")
    client = build_llm_client()
    client.refresh()
    out = Explainer(client).describe(item, block)
    assert out.strip(), "expected a non-empty vision description"
    # It must be a spoken sentence, not raw markdown / LaTeX.
    assert "|" not in out and "\\(" not in out and not out.strip().startswith("#")
