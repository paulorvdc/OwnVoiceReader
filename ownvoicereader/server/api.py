"""server/api.py — REST endpoints for loading a document + serving the PDF.

The browser talks to these to (a) discover what is loaded, (b) load a PDF by
path or by upload (drag-drop), and (c) fetch the raw PDF bytes for PDF.js.

Provider credentials are reported to the UI by *name only* (never the value)
so the user can see which secrets the OS environment exposes (A6).
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from ..config import provider_env
from ..ingest import open_pdf, build_document

log = logging.getLogger("ownvoicereader.api")


# --------------------------------------------------------------------------- #
# Loading helpers (shared with app.py)
# --------------------------------------------------------------------------- #
def load_document_by_path(state, path: str) -> None:
    """Open a PDF on disk, build its reading-ordered text blocks, and record
    it as the active document."""
    try:
        doc = open_pdf(path)
        # M1: build the full reading document (text lines -> ordered blocks +
        # sparse-page flags). A build failure shouldn't kill the load, so we
        # fall back to a metadata-only document.
        try:
            build_document(doc)
        except Exception:  # pragma: no cover - never abort the load
            log.exception("text extraction failed for %s; loading metadata only",
                          path)
        state.document = doc
        state.pdf_path = doc.source_path
        state.load_error = None
        # A fresh document invalidates any cached TTS voice binding.
        log.info("loaded document: %s (%d pages, %d blocks)",
                 doc.title, doc.page_count, len(doc.blocks))
    except Exception as exc:  # surface a friendly message to the UI
        state.load_error = str(exc)
        state.document = None
        state.pdf_path = None
        log.error("failed to load %s: %s", path, exc)


def load_uploaded(state, content: bytes, filename: str) -> None:
    """Persist an uploaded PDF to a per-session temp dir and load it."""
    tmp = state.temp_dir or Path(tempfile.gettempdir()) / "ownvoicereader"
    tmp.mkdir(parents=True, exist_ok=True)
    safe = Path(filename or "upload.pdf").name or "upload.pdf"
    dest = tmp / f"upload_{int(time.time() * 1000)}_{safe}"
    try:
        dest.write_bytes(content)
    except OSError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"could not store upload: {exc}")
    state.temp_dir = tmp
    load_document_by_path(state, str(dest))


def _info_payload(state) -> dict:
    """The JSON the viewer uses to decide what to show."""
    voice = _voice_info(state)
    if state.document is None:
        return {
            "loaded": False,
            "document": None,
            "load_error": state.load_error,
            "providers": _provider_names(),
            "voice": voice,
        }
    return {
        "loaded": True,
        "document": state.document.to_dict(),
        "load_error": None,
        "providers": _provider_names(),
        "voice": voice,
    }


def _provider_names() -> list[str]:
    """Names of provider env vars currently set in the OS environment.

    Values are NEVER included — only the presence of a secret is reported so
    the user knows what the app can (and will not) see.
    """
    return sorted(provider_env().keys())


def get_tts_engine(state) -> "Optional[object]":
    """Return the shared local TTS engine, building it once and caching it.

    ``None`` means no local engine is available; the viewer then shows voice
    controls as unavailable (graceful degradation, M6).
    """
    if getattr(state, "tts", None) is None and not getattr(state, "_tts_tried", False):
        state._tts_tried = True
        try:
            from ..voice import build_tts_engine

            state.tts = build_tts_engine(state.config, voice=state.config.voice)
        except Exception:  # pragma: no cover - never abort on TTS discovery
            log.exception("TTS engine discovery failed")
            state.tts = None
    return getattr(state, "tts", None)


def _voice_info(state) -> dict:
    """Report which local voice/style options exist, *without* audio values."""
    engine = get_tts_engine(state)
    if engine is None:
        return {"available": False, "engine": None, "voices": [], "styles": []}
    try:
        voices = engine.list_voices()
    except Exception:
        voices = []
    from ..voice import style_names

    return {
        "available": True,
        "engine": engine.name,
        "sample_rate": getattr(engine, "sample_rate", None),
        "voices": voices,
        "default_voice": getattr(engine, "_voice", None),
        "styles": style_names(),
    }


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def build_api_router(state) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["api"])

    @router.get("/info")
    async def info() -> JSONResponse:
        return JSONResponse(_info_payload(state))

    @router.get("/providers")
    async def providers() -> JSONResponse:
        return JSONResponse({"providers": _provider_names()})

    @router.get("/load")
    async def load_by_path(path: str = Query(..., description="absolute or ~ path")) -> JSONResponse:
        load_document_by_path(state, path)
        if state.document is None:
            raise HTTPException(status_code=400, detail=state.load_error or "could not load PDF")
        return JSONResponse(_info_payload(state))

    @router.post("/load")
    async def load_upload(file: UploadFile = File(...)) -> JSONResponse:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="empty upload")
        load_uploaded(state, content, file.filename or "upload.pdf")
        if state.document is None:
            raise HTTPException(status_code=400, detail=state.load_error or "could not load PDF")
        return JSONResponse(_info_payload(state))

    @router.post("/unload")
    async def unload() -> JSONResponse:
        state.document = None
        state.pdf_path = None
        state.load_error = None
        return JSONResponse(_info_payload(state))

    return router
