"""server/app.py — FastAPI application factory + server runner.

Responsibilities:
  * build a FastAPI app that serves the PDF.js viewer + the JSON API
  * bind to 127.0.0.1 on a fixed default port (6767, A13) unless the user
    pins one, or passes --port 0 for a random free high port
  * auto-open the browser (A9 default) unless --no-browser
  * keep a single in-process "reading state" (the currently loaded document)

The viewer is the source of truth for what the user is looking at; the server
only holds the loaded document so the browser can fetch /pdf and /api/info.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from ..model import ReadingDocument
from .api import build_api_router, load_document_by_path, get_tts_engine
from .stream import build_stream_router

log = logging.getLogger("ownvoicereader.server")

# The viewer assets (index.html, transport.js, vendored PDF.js) live next to
# the package:  <repo>/ownvoicereader/viewer/  ->  .../ownvoicereader/server/app.py .parent.parent/viewer
VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"


@dataclass
class ServerState:
    """Single in-process state object shared by the API + stream routers."""

    config: Config
    document: Optional[ReadingDocument] = None
    pdf_path: Optional[str] = None
    temp_dir: Optional[Path] = None
    started_at: float = field(default_factory=time.time)
    load_error: Optional[str] = None
    # Shared local TTS engine (built lazily, cached).
    tts: object = None
    _tts_tried: bool = False


def _free_port(host: str) -> int:
    """Ask the OS for a free high port (A13). Small race window is acceptable
    for a local single-user tool; we re-bind and surface the error if it bites."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        return s.getsockname()[1]


def _viewer_version(viewer: Path) -> str:
    """Short content hash of transport.js — the build the browser should run.

    It's injected into the page (``window.__OV_VER__``) and used to version-stamp
    the script URL, so a browser can never run a stale cached copy of the viewer
    on the same URL.
    """
    try:
        return hashlib.sha256((viewer / "transport.js").read_bytes()).hexdigest()[:10]
    except OSError:
        return "0000000000"


def create_app(state: ServerState) -> FastAPI:
    """Build and return the FastAPI app, closing over ``state``."""
    app = FastAPI(
        title="OwnVoiceReader",
        description="your model, your voice, your machine",
        version="0.1.0",
    )

    # JSON API + WebSocket transport
    app.include_router(build_api_router(state))
    app.include_router(build_stream_router(state))

    # Never let the browser run a stale viewer script. The app is a local,
    # single-session tool, so we force revalidation on every request: the
    # browser always checks (and gets a cheap 304 when a file is unchanged),
    # but picks up a fresh copy the moment we edit transport.js / index.html.
    # Without this, an in-development edit to the JS can be served from the
    # browser's cache — the exact class of bug that produced a malformed
    # `ws://…/ws//host/ws` URL from an old build. HTTP middleware does not
    # wrap the WebSocket scope, so /ws is unaffected.
    @app.middleware("http")
    async def _no_cache(request, call_next):
        response = await call_next(request)
        # Only add a default if the route didn't already set one — this lets
        # the index route keep its stronger "no-store" directive.
        if "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    # Static viewer assets (transport.js + vendored PDF.js).
    viewer = VIEWER_DIR
    if viewer.is_dir():
        app.mount("/viewer", StaticFiles(directory=str(viewer)), name="viewer")
    else:  # pragma: no cover - only when the package data is missing
        log.warning("viewer directory not found at %s", viewer)

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        """Serve the viewer HTML with a per-content version stamp.

        We hash ``transport.js`` and load it as ``transport.js?v=<hash>``. The
        version changes whenever the script's bytes change, so a browser can
        never run a *stale cached* transport.js on the same URL — the URL the
        browser requests is different and therefore uncached. The version is
        also exposed as ``window.__OV_VER__`` so the status bar can show which
        build is actually running. ``no-store`` keeps the HTML itself fresh.
        """
        html = (viewer / "index.html").read_text()
        script = viewer / "transport.js"
        try:
            ver = hashlib.sha256(script.read_bytes()).hexdigest()[:10]
        except OSError:
            ver = "0000000000"
        needle = '<script src="/viewer/transport.js"></script>'
        replacement = (
            f'<script>window.__OV_VER__="{ver}";</script>'
            f'<script src="/viewer/transport.js?v={ver}"></script>'
        )
        if needle in html:
            html = html.replace(needle, replacement)
        log.info("serving viewer (transport.js v%s)", ver)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get("/pdf", include_in_schema=False)
    async def get_pdf() -> FileResponse:
        """Serve the active PDF's raw bytes (top-level, not under /api).

        FileResponse supports HTTP range requests out of the box, which is what
        PDF.js wants for large files.
        """
        if not state.pdf_path or not Path(state.pdf_path).exists():
            raise HTTPException(status_code=404, detail="no document loaded")
        return FileResponse(state.pdf_path, media_type="application/pdf")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True, "started_at": state.started_at}

    return app


def _open_when_ready(base: str, open_url: str, timeout: float = 15.0) -> None:
    """Open the browser from a daemon thread once /healthz answers.

    ``base`` is the URL used to poll ``/healthz``; ``open_url`` is what we hand
    to the browser (carries a ``?v=`` token so the browser fetches a fresh page
    instead of a disk-cached one). Polling avoids the race where the browser
    opens before uvicorn has bound the port. Never blocks the main thread / loop.
    """
    import urllib.request

    def _go() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        try:
            webbrowser.open(open_url)
        except Exception as exc:  # pragma: no cover
            log.warning("could not open browser: %s", exc)

    threading.Thread(target=_go, daemon=True).start()


def _print_banner(state: ServerState, open_url: str, version: str) -> None:
    config = state.config
    doc = state.document
    banner = [
        "",
        "  OwnVoiceReader  —  your model, your voice, your machine",
        f"  document : {doc.title if doc else '(none — open one in the viewer)'}",
        f"  serving  : {open_url}",
        f"  viewer   : transport.js v{version}",
        "  local-only: " + ("yes" if config.local_only else "no"),
        "  press Ctrl-C to stop",
        "",
        "  (open the URL above — its ?v= token forces a fresh page so the",
        "   browser can't run a stale cached script)",
    ]
    print("\n".join(banner), flush=True)


def _run_headless(config: Config) -> int:
    """Read a PDF to completion without a browser (``--auto`` / CI, A12).

    Builds the document, runs the orchestrator to the end through a system audio
    player, and prints a short transcript + summary to stdout. Returns the
    number of cues spoken.
    """
    from .api import load_document_by_path
    from ..reader.orchestrator import Orchestrator
    from ..voice import build_player

    if not config.pdf_path:
        print("ownvoicereader --auto needs a PDF path (no UI in headless mode)",
              file=__import__("sys").stderr)
        return 1

    state = ServerState(config=config, pdf_path=config.pdf_path)
    load_document_by_path(state, config.pdf_path)
    if state.document is None:
        print(f"could not load PDF: {state.load_error}", file=__import__("sys").stderr)
        return 1

    doc = state.document
    tts = get_tts_engine(state)
    player = build_player(config)
    orch = Orchestrator(doc, tts, config)
    log.info("headless read: %d cue(s), engine=%s, style=%s",
             orch.n, tts.name if tts else "none",
             (config.style or "academic-calm"))

    spoken = 0
    import asyncio
    try:
        spoken = asyncio.run(orch.run_to_end(player))
    except KeyboardInterrupt:
        log.info("interrupted")

    print(
        f"\nread {spoken}/{orch.n} cue(s) of '{doc.title}' "
        f"({doc.page_count} pages, engine={tts.name if tts else 'none'})"
    )
    return spoken


def _ensure_free_port(host: str, port: int) -> int:
    """Return ``port`` if it can be bound, else a free high port.

    A fixed default (6767) is friendlier than a random one, but if that port
    is already taken we fall back to a free port rather than crash — the
    banner always prints the real URL, so the user is never stuck.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return port
        except OSError:
            pass
    free = _free_port(host)
    log.warning("port %d is busy on %s — using free port %d instead",
                port, host, free)
    return free


def start(config: Config) -> int:
    """Entry point used by the CLI.

    * ``--auto`` reads the PDF to completion headlessly (A12) and returns.
    * otherwise it serves the browser viewer on 127.0.0.1:<port>.
    """
    import atexit
    import shutil

    if config.auto:
        return _run_headless(config)

    host = config.host
    # 0 (or an unset port) means "pick a free high port"; a concrete port is
    # used as-is, but if it's busy we fall back to a free one so the app
    # always starts.
    if not config.port:
        port = _free_port(host)
    else:
        port = _ensure_free_port(host, config.port)

    state = ServerState(config=config, pdf_path=config.pdf_path)
    # Load the document eagerly when a path was given so /api/info is correct
    # the moment the browser lands on the page.
    if config.pdf_path:
        load_document_by_path(state, config.pdf_path)

    app = create_app(state)

    # Clean up any uploaded temp files on exit.
    def _cleanup() -> None:
        if state.temp_dir and state.temp_dir.exists():
            shutil.rmtree(state.temp_dir, ignore_errors=True)

    atexit.register(_cleanup)

    url = f"http://{host}:{port}/"
    # A versioned open-URL (?v=<hash>) gives the browser a URL it has never
    # cached, so it fetches a fresh index.html + transport.js instead of a
    # stale disk-cached copy — the surest way to dodge the 403 reconnect loop.
    version = _viewer_version(VIEWER_DIR)
    open_url = f"{url}?v={version}"
    _print_banner(state, open_url, version)

    if config.open_browser:
        _open_when_ready(url, open_url)

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        _cleanup()
    return port
