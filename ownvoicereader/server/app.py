"""server/app.py — FastAPI application factory + server runner.

Responsibilities (M0):
  * build a FastAPI app that serves the PDF.js viewer + the JSON API
  * bind to 127.0.0.1 on a random high port (A13) unless the user pins one
  * auto-open the browser (A9 default) unless --no-browser
  * keep a single in-process "reading state" (the currently loaded document)

The viewer is the source of truth for what the user is looking at; the server
only holds the loaded document so the browser can fetch /pdf and /api/info.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from ..model import ReadingDocument
from .api import build_api_router, load_document_by_path
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
    viewer = VIEWER_DIR
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


def start(config: Config) -> int:
    """Entry point used by the CLI: build state, serve, return the port.

    Blocks until the process is interrupted (Ctrl-C). Uses uvicorn.run so the
    lifecycle (startup + signal handling) is handled by uvicorn itself, which
    is the version-robust way to run the server programmatically.
    """
    import atexit
    import shutil

    host = config.host
    port = config.port if config.port else _free_port(host)

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
