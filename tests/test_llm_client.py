"""Tests for M2.5 checkpoint — the LLM client (`ollama` library, local Ollama).

Two layers of coverage:

  * **Hermetic** — the pieces that need no server: model-id resolution, the
    ``NullLlmClient`` fallback (graceful "vision off" state), the *no-ollama*
    branch of the factory (the library is in the ``[vision]`` extra, so a core
    install must still import and degrade), and the empty-image short-circuit of
    ``describe`` (no network).
  * **Integration** — a live chat / vision call against a real Ollama server,
    skipped automatically when none is reachable so the suite stays green in CI
    (the hermetic layer pins the contract; this layer pins that the contract
    actually works end-to-end when a model is present).
"""

from __future__ import annotations

import importlib
import socket

import pytest

from ownvoicereader.config import Config
from ownvoicereader.llm import client as c
from ownvoicereader.llm.client import (
    DEFAULT_MODEL,
    LlmUnavailable,
    NullLlmClient,
    OllamaClient,
    build_llm_client,
    is_vision_available,
    resolve_model,
)


# --------------------------------------------------------------------------- #
# Model-id resolution
# --------------------------------------------------------------------------- #
def test_resolve_model_explicit_wins():
    assert resolve_model(None, "foo") == "foo"


def test_resolve_model_from_config():
    assert resolve_model(Config(llm_model="bar"), None) == "bar"


def test_resolve_model_default():
    assert resolve_model() == DEFAULT_MODEL
    assert resolve_model(Config()) == DEFAULT_MODEL


def test_resolve_model_explicit_beats_config():
    assert resolve_model(Config(llm_model="bar"), "foo") == "foo"


# --------------------------------------------------------------------------- #
# Null client — the graceful "vision off" state
# --------------------------------------------------------------------------- #
def test_null_client_reports_unavailable():
    n = NullLlmClient()
    assert n.available is False
    assert n.has_model() is False
    assert n.list_models() == []


def test_null_client_chat_raises_unavailable():
    n = NullLlmClient()
    with pytest.raises(LlmUnavailable):
        n.chat(messages=[{"role": "user", "content": "hi"}])


def test_describe_empty_image_short_circuits():
    # An empty image must not touch the network even on a real client — it is a
    # no-op that returns "".
    assert NullLlmClient().describe(b"", "describe this") == ""


# --------------------------------------------------------------------------- #
# Factory — present vs absent library
# --------------------------------------------------------------------------- #
def test_factory_returns_ollama_when_lib_present():
    client = build_llm_client()
    # ollama is installed in this env, so we get a real OllamaClient.
    assert isinstance(client, OllamaClient)
    assert client.model == DEFAULT_MODEL


def test_factory_returns_null_when_lib_absent(monkeypatch):
    # Simulate a core install: make `import ollama` fail.
    monkeypatch.setitem(__import__("sys").modules, "ollama", None)
    try:
        client = build_llm_client()
        assert isinstance(client, NullLlmClient)
        with pytest.raises(LlmUnavailable):
            client.chat(messages=[{"role": "user", "content": "hi"}])
    finally:
        # restore the real module for the rest of the session
        monkeypatch.undo()


def test_factory_null_flag_reraises_absent(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "ollama", None)
    try:
        with pytest.raises(ImportError):
            build_llm_client(null=False)
    finally:
        monkeypatch.undo()


def test_factory_model_and_host_forwarded():
    client = build_llm_client(model="qwen3:8b", host="http://127.0.0.1:12345")
    assert isinstance(client, OllamaClient)
    assert client.model == "qwen3:8b"
    assert client._host == "http://127.0.0.1:12345"


# --------------------------------------------------------------------------- #
# Integration — a live Ollama server (skipped when none is reachable)
# --------------------------------------------------------------------------- #
def _server_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1.0):
            return True
    except OSError:
        return False


_live = _server_reachable()


@pytest.mark.skipif(not _live, reason="no Ollama server on 11434")
def test_integration_list_models():
    client = build_llm_client()
    client.refresh()
    models = client.list_models()
    assert models, "expected at least one model on the server"


@pytest.mark.skipif(not _live, reason="no Ollama server on 11434")
def test_integration_chat_returns_text():
    client = build_llm_client()
    client.refresh()
    out = client.chat(messages=[{"role": "user", "content": "Say 'pong' only."}],
                      options={"temperature": 0.0})
    assert isinstance(out, str)
    assert out.strip(), "expected a non-empty answer"


@pytest.mark.skipif(not _live, reason="no Ollama server on 11434")
def test_integration_vision_describe():
    # A tiny 1x1 PNG is enough to exercise the vision *path*; the model may not
    # say anything meaningful, but the call must round-trip without error.
    client = build_llm_client()
    client.refresh()
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    out = client.describe(tiny_png, "What do you see?", options={"temperature": 0.0})
    assert isinstance(out, str)


@pytest.mark.skipif(not _live, reason="no Ollama server on 11434")
def test_integration_is_vision_available_true():
    assert is_vision_available() is True
