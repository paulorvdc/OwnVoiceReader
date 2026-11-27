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

import logging
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from ..model import ReadingDocument
from .api import build_api_router, load_document_by_path, get_tts_engine
from .stream import build_stream_router

log = logging.getLogger("ownvoicereader.server")

def _viewer_dir() -> Path:
    """Locate the static viewer assets (index.html, transport.js, vendored PDF.js).

    In a normal (pip/conda) install the viewer lives next to the package
    (``<pkg>/viewer``). Under a frozen build (PyInstaller) the assets are
    extracted beside the executable (one-dir) or into a temp ``_MEIPASS`` dir
    (one-file) — resolve against that so the frozen app finds them too; the dev
    path is the fallback when nothing frozen is present.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable)))
        cand = base / "viewer"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parent.parent / "viewer"


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
    # Shared local LLM/vision client (built lazily, cached). M2.5.
    llm: object = None
    _llm_tried: bool = False
    # Shared local OCR engine (built lazily, cached). M3: reads sparse/scanned
    # pages so a scanned PDF reads like a typed one.
    ocr: object = None
    _ocr_tried: bool = False


def _free_port(host: str) -> int:
    """Ask the OS for a free high port (A13). Small race window is acceptable
    for a local single-user tool; we re-bind and surface the error if it bites."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        return s.getsockname()[1]


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

    # Static viewer assets (transport.js + vendored PDF.js).
    viewer = _viewer_dir()
    if viewer.is_dir():
        app.mount("/viewer", StaticFiles(directory=str(viewer)), name="viewer")
    else:  # pragma: no cover - only when the package data is missing
        log.warning("viewer directory not found at %s", viewer)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(viewer / "index.html", media_type="text/html")

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


def _open_when_ready(url: str, timeout: float = 15.0) -> None:
    """Open the browser from a daemon thread once /healthz answers.

    Polling avoids the race where the browser opens before uvicorn has bound
    the port. Never blocks the main thread / event loop.
    """
    import urllib.request

    def _go() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url + "healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover
            log.warning("could not open browser: %s", exc)

    threading.Thread(target=_go, daemon=True).start()


def _print_banner(state: ServerState, url: str) -> None:
    config = state.config
    doc = state.document
    banner = [
        "",
        "  OwnVoiceReader  —  your model, your voice, your machine",
        f"  document : {doc.title if doc else '(none — open one in the viewer)'}",
        f"  serving  : {url}",
        "  local-only: " + ("yes" if config.local_only else "no"),
        "  press Ctrl-C to stop",
        "",
    ]
    print("\n".join(banner), flush=True)


def _run_headless(config: Config) -> int:
    """Read a PDF to completion without a browser (``--auto`` / CI, A12).

    Builds the document, runs the orchestrator to the end through a system audio
    player, and prints a short transcript + summary to stdout. Returns the
    number of cues spoken.
    """
    from .api import load_document_by_path, get_llm_client
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
    llm = get_llm_client(state)
    player = build_player(config)
    orch = Orchestrator(doc, tts, config, llm=llm)
    log.info("headless read: %d cue(s), engine=%s, style=%s, vision=%s",
             orch.n, tts.name if tts else "none",
             (config.style or "academic-calm"),
             getattr(llm, "name", "none"))

    spoken = 0
    import asyncio
    try:
        spoken = asyncio.run(orch.run_to_end(player))
    except KeyboardInterrupt:
        log.info("interrupted")

    print(
        f"\nread {spoken} spoken unit(s) of '{doc.title}' "
        f"({orch.n} text cues, {doc.page_count} pages, "
        f"engine={tts.name if tts else 'none'})"
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
    _print_banner(state, url)

    if config.open_browser:
        _open_when_ready(url)

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        _cleanup()
    return port
