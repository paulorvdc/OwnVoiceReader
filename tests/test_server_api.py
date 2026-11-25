"""Tests for the server's vision-status payload (`_vision_info`, M2.5 UI badge).

The badge shows the *configured* vision model (green when reachable, red "off"
when not, so the user knows to set up Ollama). `_vision_info` is hermetically
testable: we hand it a state whose LLM client is a fake, so no Ollama server is
needed. The model it reports comes from the **client itself** (``client.model``),
falling back to the *configured* model when the client is the null one.
"""

from __future__ import annotations

from types import SimpleNamespace

from ownvoicereader.config import Config
from ownvoicereader.llm.client import DEFAULT_MODEL
from ownvoicereader.server.api import _vision_info, get_llm_client


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _state(llm, config=None, *, tried=True):
    """A minimal ServerState stand-in for get_llm_client + _vision_info.

    ``_llm_tried=True`` short-circuits get_llm_client so it returns the supplied
    client (or None) without building a real one."""
    st = SimpleNamespace()
    st.llm = llm
    st._llm_tried = tried
    st.config = config
    return st


class _FakeOllama:
    """A stand-in OllamaClient: a name, a final chosen model, and an availability."""

    name = "ollama"

    def __init__(self, model, available=True):
        self.model = model
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def refresh(self):
        return self


class _FlippingClient(_FakeOllama):
    """availability starts False and turns True after refresh() — the probe path."""

    def __init__(self, model):
        super().__init__(model, available=False)
        self.refreshed = False

    def refresh(self):
        self.refreshed = True
        self._available = True
        return self


# --------------------------------------------------------------------------- #
# model comes from the client
# --------------------------------------------------------------------------- #
def test_vision_info_available_reports_client_model():
    st = _state(_FakeOllama("llama3.2-vision", available=True), config=None)
    info = _vision_info(st)
    assert info == {"available": True, "installed": True, "model": "llama3.2-vision"}


def test_vision_info_installed_but_unreachable_is_red_with_model():
    # Extra present, model configured, but the server is down -> not available,
    # yet the badge still names the model so the user knows which to pull.
    st = _state(_FakeOllama("lfm3-vl:2b", available=False), config=None)
    info = _vision_info(st)
    assert info["available"] is False
    assert info["installed"] is True
    assert info["model"] == "lfm3-vl:2b"


# --------------------------------------------------------------------------- #
# null client (extra absent) -> falls back to the configured model
# --------------------------------------------------------------------------- #
def test_vision_info_null_client_falls_back_to_default():
    st = _state(None, config=None)
    info = _vision_info(st)
    assert info == {"available": False, "installed": False, "model": DEFAULT_MODEL}


def test_vision_info_null_client_uses_configured_model():
    st = _state(None, config=Config(llm_model="qwen3:8b"))
    info = _vision_info(st)
    assert info["available"] is False
    assert info["installed"] is False
    assert info["model"] == "qwen3:8b"


# --------------------------------------------------------------------------- #
# probe refreshes the cached availability (so "start Ollama, reload" turns green)
# --------------------------------------------------------------------------- #
def test_vision_info_probe_refreshes_availability():
    st = _state(_FlippingClient("llama3.2-vision"), config=None)
    # Without probing, the (stale) cached value is read: not yet available.
    assert _vision_info(st)["available"] is False
    # With probing, refresh() is called and the fresh value is read.
    assert _vision_info(st, probe=True)["available"] is True
    assert st.llm.refreshed is True


def test_get_llm_client_returns_cached_client():
    client = _FakeOllama("m", available=True)
    st = _state(client, config=None)
    assert get_llm_client(st) is client
