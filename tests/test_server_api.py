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
from ownvoicereader.llm.client import DEFAULT_HOST, DEFAULT_MODEL
from ownvoicereader.server.api import _settings_payload, _vision_info, get_llm_client


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

    def __init__(self, model, available=True, models=None):
        self.model = model
        self._available = available
        self._models = models or []
        self.effective_host = "http://fake:11434"   # M4: the host the picker shows

    @property
    def available(self) -> bool:
        return self._available

    def refresh(self):
        return self

    def list_cached_models(self):
        return list(self._models)


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
    assert info == {"available": True, "installed": True,
                    "model": "llama3.2-vision", "models": [],
                    "host": "http://fake:11434"}


def test_vision_info_reports_cached_model_list():
    # M4: the picker's options come from the client's cached model list,
    # cache-only (no network on the REST path).
    st = _state(_FakeOllama("lfm3-vl:2b", available=True,
                            models=["llama3.2-vision", "lfm3-vl:2b", "qwen2.5"]),
                 config=None)
    info = _vision_info(st)
    assert info["models"] == ["llama3.2-vision", "lfm3-vl:2b", "qwen2.5"]
    assert info["model"] == "lfm3-vl:2b"


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
def test_vision_info_null_client_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    st = _state(None, config=None)
    info = _vision_info(st)
    # The null client knows no host, so the payload falls back to the default.
    assert info == {"available": False, "installed": False,
                    "model": DEFAULT_MODEL, "models": [], "host": DEFAULT_HOST}


def test_vision_info_null_client_uses_configured_model():
    st = _state(None, config=Config(llm_model="qwen3:8b"))
    info = _vision_info(st)
    assert info["available"] is False
    assert info["installed"] is False
    assert info["model"] == "qwen3:8b"


def test_vision_info_reports_host():
    # M4: the vision payload carries the host the client points at, so the viewer's
    # "Refresh models" button can show where the reader is looking.
    st = _state(_FakeOllama("lfm3-vl:2b", available=True), config=None)
    st.llm.effective_host = "http://10.0.0.5:11434"
    info = _vision_info(st)
    assert info["host"] == "http://10.0.0.5:11434"
    assert info["available"] is True


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


# --------------------------------------------------------------------------- #
# M4: /api/info exposes the current settings (so the viewer can pre-fill)
# --------------------------------------------------------------------------- #
def _full_state(**cfg_kwargs):
    """A ServerState stand-in with every engine cache pre-set to None+tried, so
    the info payload never builds a real TTS/LLM/OCR engine (hermetic, no
    network)."""
    st = SimpleNamespace(
        config=Config(**cfg_kwargs),
        document=None, load_error=None, pdf_path=None, temp_dir=None,
        tts=None, _tts_tried=True,
        llm=None, _llm_tried=True,
        ocr=None, _ocr_tried=True,
    )
    return st


def test_api_info_includes_settings(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ownvoicereader.server.api import build_api_router, _info_payload

    target = tmp_path / "config.yaml"
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(target))
    st = _full_state(llm_model="qwen3:8b", voice="en_US-lessac", style="lecture")
    payload = _info_payload(st)
    assert "settings" in payload
    s = payload["settings"]
    assert s["llm_model"] == "qwen3:8b"
    assert s["voice"] == "en_US-lessac"
    assert s["style"] == "lecture"
    assert s["local_only"] is True
    assert s["config_path"] == str(target)

    # ... and it is reachable over HTTP too.
    app = FastAPI()
    app.include_router(build_api_router(st))
    client = TestClient(app)
    r = client.get("/api/info")
    assert r.status_code == 200
    assert r.json()["settings"]["llm_model"] == "qwen3:8b"


# --------------------------------------------------------------------------- #
# M4: POST /api/save persists the viewer's settings to config.yaml
# --------------------------------------------------------------------------- #
def test_post_api_save_persists(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ownvoicereader.config import save_config
    from ownvoicereader.server.api import build_api_router

    target = tmp_path / "config.yaml"
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(target))
    # `/api/save` only touches state.config, so a minimal state suffices.
    st = SimpleNamespace(config=Config())
    app = FastAPI()
    app.include_router(build_api_router(st))
    client = TestClient(app)

    r = client.post("/api/save", json={
        "voice": "en_US-lessac", "style": "lecture",
        "llm_model": "qwen3:8b", "local_only": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is True
    assert body["path"] == str(target)
    assert body["settings"]["llm_model"] == "qwen3:8b"
    # the live config was updated
    assert st.config.llm_model == "qwen3:8b"
    assert st.config.local_only is False
    # ... and written to the configured file, readable back via the layering
    assert target.exists()
    reloaded = Config.from_env(target, use_yaml=True, use_env=False)
    assert reloaded.llm_model == "qwen3:8b"
    assert reloaded.local_only is False


# --------------------------------------------------------------------------- #
# M4: _settings_payload fallbacks + a no-op (empty-body) save
# --------------------------------------------------------------------------- #
def test_settings_payload_falls_back_to_defaults(tmp_path, monkeypatch):
    # With nothing configured the picker must still show a *selection*: an unset
    # style resolves to the default preset, an unset model to DEFAULT_MODEL.
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(tmp_path / "config.yaml"))
    s = _settings_payload(_full_state())
    assert s["style"] == "academic-calm"
    assert s["llm_model"] == DEFAULT_MODEL
    assert s["local_only"] is True
    assert s["voice"] is None
    assert s["ollama_host"] is None
    assert s["config_path"] == str(tmp_path / "config.yaml")


def test_settings_payload_reports_ollama_host(tmp_path, monkeypatch):
    # M4: the saved host (if any) is reported so the viewer pre-fills its input.
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(tmp_path / "config.yaml"))
    s = _settings_payload(_full_state(ollama_host="http://10.0.0.5:11434"))
    assert s["ollama_host"] == "http://10.0.0.5:11434"


def test_post_api_save_empty_body_saves_current(tmp_path, monkeypatch):
    # "Save" with no fields in the body changes nothing, but still writes the
    # current settings — the button always persists the live config.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ownvoicereader.server.api import build_api_router

    target = tmp_path / "config.yaml"
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(target))
    st = SimpleNamespace(config=Config(llm_model="qwen3:8b"))
    app = FastAPI()
    app.include_router(build_api_router(st))
    client = TestClient(app)

    r = client.post("/api/save", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is True
    assert body["path"] == str(target)
    assert body["settings"]["llm_model"] == "qwen3:8b"
    assert target.exists()


def test_post_api_save_persists_ollama_host(tmp_path, monkeypatch):
    # M4: the "Save" button persists the Ollama host the user typed into the input.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ownvoicereader.server.api import build_api_router

    target = tmp_path / "config.yaml"
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(target))
    st = SimpleNamespace(config=Config())
    app = FastAPI()
    app.include_router(build_api_router(st))
    client = TestClient(app)

    r = client.post("/api/save", json={"ollama_host": "http://10.0.0.5:11434"})
    assert r.status_code == 200
    assert r.json()["settings"]["ollama_host"] == "http://10.0.0.5:11434"
    assert st.config.ollama_host == "http://10.0.0.5:11434"   # updated live
    assert "ollama_host" in target.read_text()               # ...and persisted
