"""Tests for the desktop window host (M6 Phase 1, ownvoicereader/server/window.py).

These run without a display or a native backend: the threaded uvicorn + /healthz poll
are real, while the *window open* is simulated by monkeypatching, so the host is
verifiable on a headless CI box. The window is the primary path; the browser fallback
is a last resort — both branches are covered here.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from ownvoicereader.config import Config
from ownvoicereader.server.app import ServerState, _free_port, create_app
from ownvoicereader.server import window


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def test_serve_in_thread_serves_healthz():
    """The background-threaded uvicorn actually serves, then stops cleanly."""
    app = create_app(ServerState(config=Config()))
    port = _free_port("127.0.0.1")
    server, thread = window.serve_in_thread(app, "127.0.0.1", port)
    try:
        assert window.wait_until_ready(f"http://127.0.0.1:{port}/", timeout=10.0) is True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3.0) as r:
            assert json.loads(r.read())["ok"] is True
    finally:
        window.stop_server(server, thread)
    assert not thread.is_alive()


def test_wait_until_ready_times_out_on_dead_port():
    # Port 1 is privileged/dead: the poll never answers 200, so it returns False.
    assert window.wait_until_ready("http://127.0.0.1:1/healthz", timeout=0.4) is False


def test_once_runs_the_callback_at_most_once():
    calls = []
    once = window._once(lambda: calls.append(1))
    once()
    once()
    once()
    assert calls == [1]


# --------------------------------------------------------------------------- #
# the window is the primary path; opening it runs on_close exactly once
# --------------------------------------------------------------------------- #
def test_launch_window_opens_then_cleans_up(monkeypatch):
    """When the window opens and is closed, on_close (the temp-dir cleanup) runs once."""
    app = create_app(ServerState(config=Config()))
    port = _free_port("127.0.0.1")
    closed = []

    def _fake_open(url, *, on_close=None, **_kwargs):
        # Simulate a window that opens, is used, then closed: run its close handler.
        if on_close is not None:
            on_close()

    monkeypatch.setattr(window, "open_native_window", _fake_open)
    result = window.launch_window(app, "127.0.0.1", port, open_browser=False,
                                 on_close=lambda: closed.append(1))
    assert result == port
    assert closed == [1]  # cleanup ran exactly once, on close


# --------------------------------------------------------------------------- #
# last resort: when the window can't open, fall back to the browser (loud, once)
# --------------------------------------------------------------------------- #
def test_launch_window_falls_back_to_browser(monkeypatch):
    app = create_app(ServerState(config=Config()))
    port = _free_port("127.0.0.1")
    closed = []
    fell_back = []

    def _no_window(url, *, on_close=None, **_kwargs):
        raise window.WindowUnavailable("no native backend in this test")

    # Replace the fallback with a no-op so the test does not block on Ctrl-C; the
    # real fallback keeps the server alive until the user stops it.
    monkeypatch.setattr(window, "_fall_back_to_browser",
                        lambda *a, **k: fell_back.append(True))
    monkeypatch.setattr(window, "open_native_window", _no_window)

    result = window.launch_window(app, "127.0.0.1", port, open_browser=False,
                                 on_close=lambda: closed.append(1))
    assert result == port
    assert fell_back == [True]     # the last-resort path ran
    assert closed == [1]           # and cleanup still ran exactly once


def test_open_native_window_unavailable(monkeypatch):
    """When no backend can open, open_native_window raises WindowUnavailable."""
    try:
        import webview
    except Exception:
        # pywebview not installed: the import inside open_native_window fails.
        with pytest.raises(window.WindowUnavailable):
            window.open_native_window("http://127.0.0.1:1/")
        return
    # pywebview present but no usable backend: force start() to fail.
    def _boom(*_a, **_k):
        raise RuntimeError("no backend in this test")

    monkeypatch.setattr(webview, "start", _boom)
    with pytest.raises(window.WindowUnavailable):
        window.open_native_window("http://127.0.0.1:1/", on_close=lambda: None)
