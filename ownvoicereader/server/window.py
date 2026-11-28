"""server/window.py — the desktop window host (M6 Phase 1).

The headline M6 deliverable: open the running viewer in its **own native window** —
a Discord-like "the window *is* the app" experience, not a browser tab — via
**pywebview**'s Qt backend (QtWebEngine; native and self-contained, which is what a
packaged binary wants). The *current* vanilla-JS UI loads unchanged at
``http://127.0.0.1:<port>/``.

Thread model — the constraint that shapes everything
----------------------------------------------------
Webview backends require the **main thread**, so uvicorn runs in a **background
thread** and the window owns the main thread. Shutdown is driven by the window's
*close event*, not by ``atexit``: when a window close kills the process some
platforms never run ``atexit``, so the temp-dir cleanup is hooked to the close.

The window is the primary path; the browser is a last resort
------------------------------------------------------------
The native window is what runs. If — and only if — no native window can be opened
(pywebview missing, no backend, no display) we fall back to opening the URL in the
user's browser *and keep the server alive*, so the app is never left un-started.
That fallback is a **loud last resort** (a prominent stderr line), never the easy
path: it is what a packaged binary does so an end user is never stuck, not a
shortcut the developer leans on. It is also what makes the host testable on a
headless CI box (no window) while still opening a real window on a desktop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional

import uvicorn

from .app import _open_when_ready

log = logging.getLogger("ownvoicereader.window")

# Window geometry — a comfortable reader, resizable down to a usable minimum.
DEFAULT_TITLE = "OwnVoiceReader"
DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 760
DEFAULT_MIN_SIZE = (900, 600)

# QtWebEngine (Chromium) refuses to start under a restrictive sandbox unless told
# otherwise — the case in most packaged/container/AppImage contexts, and a real
# reliability need (not a dev convenience). Set via setdefault so a user's own
# choice still wins. The platform (xcb vs wayland vs …) is left to Qt: a window
# opens on either here, so we do not force it.
_QTWEBENGINE_DEFAULT_FLAGS = "--no-sandbox --disable-gpu"


class WindowUnavailable(Exception):
    """No native window backend / display is available; the caller falls back to a browser."""


def _apply_webview_env() -> None:
    """Set the Qt/Chromium env a packaged window needs, without clobbering a user's."""
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _QTWEBENGINE_DEFAULT_FLAGS)


def serve_in_thread(app, host: str, port: int):
    """Start uvicorn in a background thread, so the main thread is free for the window.

    Returns ``(server, thread)``. Stop it with ``server.should_exit = True`` then
    ``thread.join()`` — unlike the blocking ``uvicorn.run`` the browser path uses,
    this is *stoppable*, which the window's close handler needs.
    """
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    # uvicorn installs SIGINT/SIGTERM handlers, which are illegal off the main
    # thread; the window's close drives shutdown instead.
    server.install_signal_handlers = lambda: None

    def _run() -> None:
        # Server.serve() is a coroutine; run it in this thread's own event loop so
        # the background thread actually serves (a bare target=server.serve would
        # just create the coroutine and never await it).
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, name="uvicorn-window", daemon=True)
    thread.start()
    return server, thread


def wait_until_ready(url: str, timeout: float = 15.0) -> bool:
    """Poll /healthz until the server answers 200 (or the deadline).

    Mirrors the browser path's ``_open_when_ready`` loop, so window-open timing is
    identical. Returns True when ready, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "healthz", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _once(fn: Callable[[], None]) -> Callable[[], None]:
    """Run ``fn`` at most once, no matter how many callers invoke the wrapper.

    The window's ``closing`` event *and* a post-``start()`` guard can both fire, so
    the temp-dir cleanup must run exactly once.
    """
    done = threading.Event()
    def _wrapped() -> None:
        if done.is_set():
            return
        done.set()
        try:
            fn()
        except Exception as exc:  # pragma: no cover - cleanup must never abort
            log.warning("window cleanup failed: %s", exc)
    return _wrapped


def stop_server(server, thread, on_close: Optional[Callable[[], None]] = None) -> None:
    """Stop the server thread and (optionally) run ``on_close`` (the temp-dir cleanup).

    ``should_exit`` / ``join`` are harmless to repeat; the once-guard lives at the
    call site (:func:`_once`), so this is safe to call more than once.
    """
    server.should_exit = True
    if thread.is_alive():
        thread.join(timeout=5.0)
    if on_close:
        on_close()


def open_native_window(url: str, *, on_close: Optional[Callable[[], None]] = None,
                       title: str = DEFAULT_TITLE, width: int = DEFAULT_WIDTH,
                       height: int = DEFAULT_HEIGHT,
                       min_size=tuple(DEFAULT_MIN_SIZE)) -> None:
    """Open the running viewer in its own native window, blocking the main thread.

    Raises :class:`WindowUnavailable` if pywebview or its backend/display is
    unavailable (the caller then falls back to the browser). ``on_close`` is invoked
    once when the window closes — hooked on the ``closing`` event *and* after
    ``start()`` returns, so cleanup runs even if the close kills the loop.
    """
    _apply_webview_env()
    try:
        import webview
    except Exception as exc:  # pragma: no cover - pywebview is the desktop extra
        raise WindowUnavailable(f"pywebview is not available: {exc}") from exc

    once_close = _once(on_close) if on_close else None
    try:
        window = webview.create_window(
            title, url, width=width, height=height, min_size=tuple(min_size),
        )
        if once_close is not None:
            window.events.closing += once_close
        webview.start()  # blocks on the main thread until the window is closed
    except Exception as exc:
        raise WindowUnavailable(f"the native window could not open: {exc}") from exc
    # Some platforms return from start() without firing `closing`; run once more.
    if once_close is not None:
        once_close()


def launch_window(app, host: str, port: int, *, url: Optional[str] = None,
                  open_browser: bool = True,
                  on_close: Optional[Callable[[], None]] = None,
                  title: str = DEFAULT_TITLE, width: int = DEFAULT_WIDTH,
                  height: int = DEFAULT_HEIGHT,
                  min_size=tuple(DEFAULT_MIN_SIZE)) -> int:
    """Open the viewer in its own native window; fall back to the browser as a last resort.

    Starts uvicorn in a background thread, waits for /healthz, then opens the window
    on the main thread. On close — or, as a last resort, on any window failure or a
    server that never comes up — the server is stopped and ``on_close`` (the
    temp-dir cleanup) runs exactly once. The browser fallback is loud (a stderr line)
    because it is a last resort, not the normal path. Returns the bound port.
    """
    url = url or f"http://{host}:{port}/"
    server, thread = serve_in_thread(app, host, port)
    stop = _once(lambda: stop_server(server, thread, on_close))

    opened_window = False
    try:
        if not wait_until_ready(url, timeout=15.0):
            raise WindowUnavailable("server did not become ready in time")
        open_native_window(url, on_close=stop, title=title, width=width,
                           height=height, min_size=min_size)
        opened_window = True
    except WindowUnavailable as exc:
        # Last resort: the native window could not open. Open the browser so the app
        # is never left un-started, and say so loudly.
        _fall_back_to_browser(server, thread, url, open_browser, reason=exc)
    finally:
        # A normal window close already ran `stop` via the close handler; only stop
        # here if we got this far without a window (a fallback, or a not-ready server).
        if not opened_window:
            stop()
    return port


def _fall_back_to_browser(server, thread, url: str, open_browser: bool, *,
                          reason: Exception) -> None:
    """Loud last-resort fallback: open the browser and keep the server alive.

    The threaded uvicorn is already serving, so just open the browser (if allowed)
    and keep the main thread alive until the user stops the server (Ctrl-C) — one
    server, no second bind on the port.
    """
    log.warning("native window unavailable (%s); falling back to the browser", reason)
    print(
        "\n[OwnVoiceReader] Could not open a native window "
        f"({reason}); opening in your browser instead.\n",
        file=sys.stderr,
    )
    if open_browser:
        _open_when_ready(url)
    _block_foreground(server, thread)


def _block_foreground(server, thread) -> None:
    """Keep the main thread alive until the user stops the server (Ctrl-C).

    The threaded uvicorn serves the browser; this just waits so the process doesn't
    exit first. On SIGINT we ask the server to exit, then join. Off the main thread
    (e.g. a test) the signal install is skipped and we simply join.
    """
    import signal
    def _sigint(signum, frame):
        server.should_exit = True
    try:
        signal.signal(signal.SIGINT, _sigint)
    except (ValueError, OSError):  # not the main thread, or unsupported
        pass
    thread.join()
