"""M4: live switching of model/voice/style + the "Save settings" endpoint.

The viewer changes a setting *live* (a WS `set_model` / `set_voice` / `set_style` /
`set_local_only` frame updates the orchestrator) and persists it *deliberately*
(POST /api/save -> config.yaml). These tests exercise the two halves directly:
the orchestrator's live setters, and the server's `save_settings` writer.
"""

from __future__ import annotations

from types import SimpleNamespace

from ownvoicereader.config import Config
from ownvoicereader.model import ReadingDocument
from ownvoicereader.reader.orchestrator import Orchestrator
from ownvoicereader.server.api import save_settings


# --------------------------------------------------------------------------- #
# orchestrator live switching
# --------------------------------------------------------------------------- #
def _orch(llm=None):
    return Orchestrator(ReadingDocument(), None, Config(), llm=llm)


def test_set_voice_and_style():
    orch = _orch()
    assert orch.set_voice("en_US-lessac") == "en_US-lessac"
    assert orch.set_style("lecture") == "lecture"


def test_set_model_updates_config_and_client():
    llm = SimpleNamespace(model="old")
    orch = _orch(llm)
    out = orch.set_model("qwen3:8b")
    assert out == "qwen3:8b"
    assert orch.config.llm_model == "qwen3:8b"   # recorded for "Save"
    assert llm.model == "qwen3:8b"               # applied to the shared client live


def test_set_model_no_client_still_records_config():
    orch = _orch(llm=None)
    assert orch.set_model("m") == "m"
    assert orch.config.llm_model == "m"


def test_set_local_only_updates_config():
    orch = _orch()
    assert orch.set_local_only(False) is False
    assert orch.config.local_only is False
    assert orch.set_local_only(True) is True
    assert orch.config.local_only is True


def test_status_carries_model_and_local_only():
    orch = _orch(SimpleNamespace(model="m"))
    orch.set_model("qwen3:8b")
    st = orch.status()
    assert st["llm_model"] == "qwen3:8b"
    assert "local_only" in st
    assert st["local_only"] is True


def test_set_style_invalid_degrades_to_default():
    # A bad style name must not crash the live switch: get_style resolves an
    # unknown name to the default preset, so the reader keeps going.
    orch = _orch()
    assert orch.set_style("does-not-exist") == "academic-calm"
    assert orch.status()["style"] == "academic-calm"


def test_set_voice_none_resets():
    # A live switch can also *clear* a voice (back to the engine default).
    orch = _orch()
    assert orch.set_voice("en_US-lessac") == "en_US-lessac"
    assert orch.set_voice(None) is None
    assert orch.status()["voice"] is None


def test_status_without_config_defaults():
    # The status reply degrades cleanly when there is no shared config: the model
    # is None and local_only defaults to True (the safe, no-cloud-egress posture).
    orch = _orch()
    orch.config = None
    st = orch.status()
    assert st["llm_model"] is None
    assert st["local_only"] is True


def test_set_ollama_host_updates_client_and_config():
    # M4: the "Refresh models" host switch re-points the *shared* client (so the next
    # probe/load reaches the new server) and records it on the config (so "Save"
    # persists it).
    client = SimpleNamespace(model="m", host=None, set_host=None)
    client.set_host = lambda h: setattr(client, "host", h)
    orch = _orch(client)
    assert orch.set_ollama_host("http://10.0.0.5:11434") == "http://10.0.0.5:11434"
    assert client.host == "http://10.0.0.5:11434"               # applied to the client
    assert orch.config.ollama_host == "http://10.0.0.5:11434"   # recorded for "Save"


def test_set_ollama_host_none_reverts_config():
    # An empty / None host reverts the saved setting to "use OLLAMA_HOST / default".
    orch = _orch()
    orch.config.ollama_host = "http://old:11434"
    assert orch.set_ollama_host(None) is None
    assert orch.config.ollama_host is None


# --------------------------------------------------------------------------- #
# the "Save settings" writer
# --------------------------------------------------------------------------- #
def _state(**cfg_kwargs):
    return SimpleNamespace(config=Config(**cfg_kwargs))


def test_save_settings_persists_and_updates_config(tmp_path):
    st = _state()
    result = save_settings(st, voice="en_US-lessac", style="lecture",
                           llm_model="qwen3:8b", local_only=False,
                           path=tmp_path / "config.yaml")
    # the live config was updated
    assert st.config.voice == "en_US-lessac"
    assert st.config.style == "lecture"
    assert st.config.llm_model == "qwen3:8b"
    assert st.config.local_only is False
    # and it was written to disk
    assert (tmp_path / "config.yaml").exists()
    assert result["path"] == str(tmp_path / "config.yaml")
    assert result["settings"]["llm_model"] == "qwen3:8b"
    assert result["settings"]["local_only"] is False


def test_save_settings_ignores_unset(tmp_path):
    st = _state(llm_model="from_config")
    # Only voice is provided; the others stay at their current (config) value.
    save_settings(st, voice="v", path=tmp_path / "config.yaml")
    assert st.config.voice == "v"
    assert st.config.llm_model == "from_config"   # untouched


def test_save_settings_writes_only_durable_fields(tmp_path):
    st = _state(llm_model="m", voice="v", style="narrative", local_only=True,
                playback="system")
    save_settings(st, llm_model="m", path=tmp_path / "config.yaml")
    written = (tmp_path / "config.yaml").read_text()
    # transient per-run fields must not leak into the durable file
    assert "pdf_path" not in written
    assert "checkpoint_path" not in written
    # durable fields are present
    for key in ("llm_model", "voice", "style", "local_only", "playback"):
        assert key in written


# --------------------------------------------------------------------------- #
# M4: the WS dispatch for the live-switch commands (real stream.py handler)
# --------------------------------------------------------------------------- #
def _stream_state():
    """A ServerState stand-in with every engine cache pre-set to None+tried, so
    the WS handshake never builds a real TTS/LLM/OCR engine (hermetic). An empty
    (but non-None) document avoids the "no document loaded" notice frame."""
    st = SimpleNamespace(
        config=Config(),
        document=ReadingDocument(), load_error=None, pdf_path=None, temp_dir=None,
        tts=None, _tts_tried=True,
        llm=None, _llm_tried=True,
        ocr=None, _ocr_tried=True,
    )
    return st


def test_ws_set_model_and_local_only(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from ownvoicereader.server.api import build_api_router
    from ownvoicereader.server.stream import build_stream_router

    target = tmp_path / "config.yaml"
    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(target))
    st = _stream_state()
    app = FastAPI()
    app.include_router(build_stream_router(st))
    app.include_router(build_api_router(st))
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"

        # Live-switch the model; the state reply carries it back and it lands on
        # the shared config (so a later "Save" persists it).
        ws.send_json({"cmd": "set_model", "llm_model": "qwen3:8b"})
        st_reply = ws.receive_json()
        assert st_reply["type"] == "state"
        assert st_reply["llm_model"] == "qwen3:8b"
        assert st.config.llm_model == "qwen3:8b"

        # Live-toggle local-only the same way.
        ws.send_json({"cmd": "set_local_only", "local_only": False})
        lo_reply = ws.receive_json()
        assert lo_reply["local_only"] is False
        assert st.config.local_only is False

    # ... and the deliberate "Save" persists the live changes to config.yaml
    # (pointed at a temp file by the env override, never the user's real one).
    r = client.post("/api/save", json={"local_only": False, "llm_model": "qwen3:8b"})
    assert r.status_code == 200
    assert target.exists()
    reloaded = Config.load(target)
    assert reloaded.local_only is False
    assert reloaded.llm_model == "qwen3:8b"


def test_ws_set_voice_and_style_resync(tmp_path, monkeypatch):
    """The M2.5 live-switch family (voice + style): a `state` reply carries the
    new value back so the viewer can resync its pickers. (These affect this
    connection's read; they are persisted only when the user then clicks "Save".)"""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from ownvoicereader.server.api import build_api_router
    from ownvoicereader.server.stream import build_stream_router

    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(tmp_path / "config.yaml"))
    st = _stream_state()
    app = FastAPI()
    app.include_router(build_stream_router(st))
    app.include_router(build_api_router(st))
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

        ws.send_json({"cmd": "set_voice", "voice": "en_US-lessac"})
        reply = ws.receive_json()
        assert reply["type"] == "state"
        assert reply["voice"] == "en_US-lessac"

        ws.send_json({"cmd": "set_style", "style": "lecture"})
        reply = ws.receive_json()
        assert reply["type"] == "state"
        assert reply["style"] == "lecture"


def test_ws_set_model_accepts_model_key_and_local_only_defaults_true(
    tmp_path, monkeypatch
):
    """Two fallback branches the happy-path test doesn't cover: a `set_model`
    frame using the ``"model"`` key alias, and a `set_local_only` frame that
    omits the value (the handler defaults it to True — the safe posture)."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from ownvoicereader.server.api import build_api_router
    from ownvoicereader.server.stream import build_stream_router

    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(tmp_path / "config.yaml"))
    st = _stream_state()
    app = FastAPI()
    app.include_router(build_stream_router(st))
    app.include_router(build_api_router(st))
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

        ws.send_json({"cmd": "set_model", "model": "qwen3:8b"})  # the "model" alias
        reply = ws.receive_json()
        assert reply["type"] == "state"
        assert reply["llm_model"] == "qwen3:8b"
        assert st.config.llm_model == "qwen3:8b"  # recorded on the shared config

        ws.send_json({"cmd": "set_local_only"})  # value omitted -> defaults True
        reply = ws.receive_json()
        assert reply["type"] == "state"
        assert reply["local_only"] is True
        assert st.config.local_only is True


class _FakeHostClient:
    """A hermetic vision client for the WS host-switch test: it records the host
    `set_ollama_host` applies, reports that host via `effective_host`, and serves a
    fixed probed model list — so the `vision` reply is fully testable with no real
    Ollama server. (An empty ReadingDocument means the orchestrator's vision thread
    has nothing to precompute, so the client is never actually called.)"""

    name = "ollama"
    model = "lfm3-vl:2b"

    def __init__(self):
        self._host = None
        self._models = ["lfm3-vl:2b", "qwen3:8b"]

    @property
    def available(self) -> bool:
        return True

    def set_host(self, host):
        self._host = host or None
        return self

    @property
    def effective_host(self):
        return self._host or "http://localhost:11434"

    def refresh(self):
        return self

    def list_cached_models(self):
        return list(self._models)


def test_ws_set_ollama_host_returns_vision_frame(tmp_path, monkeypatch):
    """M4: the "Refresh models" button. `set_ollama_host` re-points the shared client
    and re-probes; the reply is a `vision` frame carrying the fresh model list + the
    host the reader now points at, and the host is recorded on the shared config."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from ownvoicereader.server.api import build_api_router
    from ownvoicereader.server.stream import build_stream_router

    monkeypatch.setenv("OWNVOICEREADER_CONFIG", str(tmp_path / "config.yaml"))
    st = _stream_state()
    st.llm = _FakeHostClient()        # the shared client the orchestrator drives
    st._llm_tried = True
    app = FastAPI()
    app.include_router(build_stream_router(st))
    app.include_router(build_api_router(st))
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

        ws.send_json({"cmd": "set_ollama_host", "host": "http://10.0.0.5:11434"})
        reply = ws.receive_json()
        assert reply["type"] == "vision"
        assert reply["host"] == "http://10.0.0.5:11434"
        assert "model" in reply["vision"]
        assert reply["vision"]["available"] is True
        assert st.config.ollama_host == "http://10.0.0.5:11434"  # recorded for "Save"
