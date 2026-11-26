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
from types import SimpleNamespace

import pytest

from ownvoicereader.config import Config
from ownvoicereader.llm import client as c
from ownvoicereader.llm.client import (
    DEFAULT_HOST,
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
# M4: the probed model list is cached so the REST /api/info path can list
# models for the viewer's picker *without* a network round-trip per request.
# These are hermetic: a fake handle stands in for the `ollama` server, so no
# Ollama process is needed.
# --------------------------------------------------------------------------- #
class _FakeHandle:
    """A stand-in for the `ollama` client handle: a fixed model list on .list()."""

    def __init__(self, models):
        self._models = [SimpleNamespace(model=m) for m in models]

    def list(self):
        resp = SimpleNamespace()
        resp.models = self._models
        return resp


def test_ollama_cached_models_empty_before_probe():
    client = OllamaClient(model="qwen3:8b")
    client._client = _FakeHandle(["qwen3:8b", "lfm3-vl:2b"])
    # Nothing probed yet -> the picker starts empty (populated by the WS hello).
    assert client.list_cached_models() == []


def test_ollama_probe_caches_model_list():
    client = OllamaClient(model="qwen3:8b")
    client._client = _FakeHandle(["qwen3:8b", "lfm3-vl:2b"])
    assert client.available is True  # runs the probe, populating the cache
    assert client.list_cached_models() == ["qwen3:8b", "lfm3-vl:2b"]


def test_ollama_probe_caches_list_even_when_model_absent():
    # A reachable server with a *different* model still degrades to the A2 base,
    # but the probed list is cached so the picker knows what is pullable.
    client = OllamaClient(model="not-here")
    client._client = _FakeHandle(["qwen3:8b"])
    assert client.available is True  # does not raise even though "not-here" is absent
    assert client.list_cached_models() == ["qwen3:8b"]


def test_refresh_clears_cached_models_and_availability():
    client = OllamaClient(model="qwen3:8b")
    client._client = _FakeHandle(["qwen3:8b"])
    assert client.available is True
    assert client.list_cached_models() == ["qwen3:8b"]
    client.refresh()  # "start Ollama later, reload" -> re-probe the next check
    assert client.list_cached_models() == []
    assert client._available is None


def test_null_client_cached_models_empty():
    # The graceful fallback knows no models (vision is off).
    assert NullLlmClient().list_cached_models() == []


# --------------------------------------------------------------------------- #
# M4: the saved/overridden Ollama host points the client at a different server.
# --------------------------------------------------------------------------- #
def test_build_llm_client_uses_config_ollama_host():
    client = build_llm_client(Config(ollama_host="http://10.0.0.5:11434"))
    assert client._host == "http://10.0.0.5:11434"


def test_build_llm_client_explicit_host_beats_config():
    client = build_llm_client(Config(ollama_host="http://cfg:11434"), host="http://explicit:11434")
    assert client._host == "http://explicit:11434"


def test_ollama_set_host_updates_and_invalidates():
    # Re-pointing rebuilds the cached handle + clears the probe/model caches, so the
    # next `available` / `list_models` reaches the *new* server.
    client = OllamaClient(model="m", host="http://a:11434")
    client._client = object()     # a cached handle for the old host
    client._available = True
    client._models = ["old"]
    client.set_host("http://b:11434")
    assert client._host == "http://b:11434"
    assert client._client is None      # the handle is rebuilt for the new host
    assert client._available is None    # reachability is re-probed
    assert client._models is None       # the model-list cache is cleared


def test_ollama_set_host_none_or_empty_reverts_to_env_then_default(monkeypatch):
    client = OllamaClient(model="m", host="http://a:11434")
    client.set_host(None)
    assert client._host is None
    monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
    assert client.effective_host == "http://from-env:11434"
    client.set_host("")              # an empty string reverts too
    assert client._host is None
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert client.effective_host == DEFAULT_HOST


def test_null_client_set_host_is_noop():
    # The graceful fallback is unaffected by a host change.
    n = NullLlmClient()
    assert n.set_host("http://x:11434") is n


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
