"""server/api.py — REST endpoints for loading a document + serving the PDF.

The browser talks to these to (a) discover what is loaded, (b) load a PDF by
path or by upload (drag-drop), and (c) fetch the raw PDF bytes for PDF.js.

Provider credentials are reported to the UI by *name only* (never the value)
so the user can see which secrets the OS environment exposes (A6).
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from ..config import default_config_path, provider_env
from ..ingest import open_pdf, build_document

log = logging.getLogger("ownvoicereader.api")
# --------------------------------------------------------------------------- #
def load_document_by_path(state, path: str) -> None:
    """Open a PDF on disk, build its reading-ordered text blocks (and OCR any
    page lacking a text layer, M3), and record it as the active document."""
    try:
        doc = open_pdf(path)
        # M1: build the full reading document (text lines -> ordered blocks +
        # sparse-page flags); M3 also OCRs sparse pages so a scanned PDF reads
        # like a typed one. A build failure shouldn't kill the load, so we fall
        # back to a metadata-only document.
        try:
            build_document(doc, ocr=get_ocr_engine(state))
        except Exception:  # pragma: no cover - never abort the load
            log.exception("text extraction failed for %s; loading metadata only",
                          path)
        state.document = doc
        state.pdf_path = doc.source_path
        state.load_error = None
        # A fresh document invalidates any cached TTS voice binding.
        log.info("loaded document: %s (%d pages, %d blocks, %d OCR'd)",
                 doc.title, doc.page_count, len(doc.blocks), len(doc.ocr_pages))
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


def save_settings(state, *, voice=None, style=None, llm_model=None,
                  ollama_host=None, local_only=None, playback=None,
                  path=None) -> dict:
    """Apply the user's chosen settings to the live config and persist them to
    config.yaml (the "Save" button, M4).

    Each provided value updates ``state.config`` (so it takes effect live for the
    next document load / re-read); unset ones are left as-is. The host (``ollama_host``)
    points the vision client at a different Ollama server; a credential is *never*
    among these fields. The whole
    :class:`~ownvoicereader.config.Config` is then written via the layering
    (defaults -> this file -> env -> CLI), so the file holds *only* the
    user's durable choices. Returns ``{"path", "settings"}`` for the UI.
    """
    from ..config import save_config

    cfg = state.config
    if voice is not None:
        cfg.voice = voice
    if style is not None:
        cfg.style = style
    if llm_model is not None:
        cfg.llm_model = llm_model
    if ollama_host is not None:
        cfg.ollama_host = ollama_host or None
    if local_only is not None:
        cfg.local_only = bool(local_only)
    if playback is not None:
        cfg.playback = playback
    written = save_config(cfg, path)
    return {"path": str(written), "settings": cfg.to_yaml_dict()}


def _info_payload(state) -> dict:
    """The JSON the viewer uses to decide what to show."""
    voice = _voice_info(state)
    settings = _settings_payload(state)
    if state.document is None:
        return {
            "loaded": False,
            "document": None,
            "load_error": state.load_error,
            "providers": _provider_names(),
            "voice": voice,
            "vision": _vision_info(state),
            "ocr": _ocr_info(state),
            "settings": settings,
        }
    return {
        "loaded": True,
        "document": state.document.to_dict(),
        "load_error": None,
        "providers": _provider_names(),
        "voice": voice,
        "vision": _vision_info(state),
        "ocr": _ocr_info(state),
        "settings": settings,
    }


def _settings_payload(state) -> dict:
    """The *current* settings the viewer pre-fills its pickers from, plus where
    they are saved. At page load this reflects the config layers (yaml -> env ->
    CLI); live in-session changes (M4) are reflected back via WS ``state`` frames
    and persisted by ``POST /api/save``."""
    from ..llm import resolve_model

    cfg = state.config
    return {
        "voice": cfg.voice,
        "style": cfg.style or "academic-calm",
        "llm_model": cfg.llm_model or resolve_model(cfg),
        "ollama_host": cfg.ollama_host,
        "local_only": cfg.local_only,
        "playback": cfg.playback,
        "config_path": str(default_config_path()),
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
    controls as unavailable (graceful degradation).
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


def get_llm_client(state) -> "Optional[object]":
    """Return the shared local LLM / vision client, building it once and caching it.

    ``None`` means the ``[vision]`` extra is not installed — graceful: the reader
    then speaks the A2 caption+context base. The client is built *eagerly* (cheap,
    no network) but **not** probed here; the orchestrator probes the server lazily
    in its background thread so a slow or absent server never delays page load.
    """
    if getattr(state, "llm", None) is None and not getattr(state, "_llm_tried", False):
        state._llm_tried = True
        try:
            from ..llm import build_llm_client

            state.llm = build_llm_client(state.config)
        except Exception:  # pragma: no cover - never abort on LLM discovery
            log.exception("LLM client discovery failed")
            state.llm = None
    return getattr(state, "llm", None)


def get_ocr_engine(state) -> "Optional[object]":
    """Return the shared local OCR engine, building it once and caching it.

    The engine reads *sparse* pages (no text layer) so a scanned PDF reads like a
    a typed one (M3). Construction is cheap — the heavy OCR model loads lazily on
    the first sparse page — so this is safe to call on every load. ``None`` (a
    :class:`NullOcrEngine`) means no OCR engine is installed: sparse pages simply
    stay unreadable and the typed pages still read (graceful degradation).
    """
    if getattr(state, "ocr", None) is None and not getattr(state, "_ocr_tried", False):
        state._ocr_tried = True
        try:
            from ..ingest import build_ocr_engine

            state.ocr = build_ocr_engine()
        except Exception:  # pragma: no cover - never abort on OCR discovery
            log.exception("OCR engine discovery failed")
            state.ocr = None
    return getattr(state, "ocr", None)


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


def _ocr_info(state) -> dict:
    """Report which OCR engine is in use for the UI, *without* touching the model.

    ``available`` is False when only the graceful :class:`NullOcrEngine` is in
    hand (no ``[ocr]`` extra installed) — then sparse pages stay unreadable. The
    check is cache-only: :func:`get_ocr_engine` builds the wrapper lazily and the
    heavy model loads only on the first sparse page, so this never blocks a load.
    """
    engine = get_ocr_engine(state)
    name = getattr(engine, "name", None)
    installed = engine is not None and name != "null"
    return {
        "available": bool(installed and getattr(engine, "available", False)),
        "installed": bool(installed),
        "engine": name,
    }


def _vision_info(state, *, probe: bool = False) -> dict:
    """Report the vision model's status for the UI badge: which model is in use
    and whether it is reachable *now*.

    ``model`` is the **client's** final chosen id (``client.model`` — the
    explicit ``--llm`` / ``OWNVOICEREADER_LLM_MODEL`` > default resolution the
    client actually runs on), so the badge shows exactly the model the reader
    will use. When vision is off the client is the ``NullLlmClient`` (no
    ``model``), so we fall back to the *configured* model — that way the red
    "set it up" badge still names the model to pull.

    ``available`` reflects reachability: with ``probe`` the client's cache is
    refreshed first, so starting Ollama and reloading turns the badge green; the
    REST path stays cache-only (no probe per request). The probe is one cached
    round-trip, shared with the orchestrator's background thread, so it never
    blocks page load.
    """
    from ..llm import resolve_model
    from ..llm.client import DEFAULT_HOST

    client = get_llm_client(state)
    installed = client is not None and getattr(client, "name", "null") != "null"
    model = getattr(client, "model", None) or resolve_model(state.config)
    # M4: the host the client points at (saved / OLLAMA_HOST / default), so the
    # viewer's "Refresh models" can show where the reader is looking.
    host = getattr(client, "effective_host", None) or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    if not installed:
        return {"available": False, "installed": False, "model": model,
                "models": [], "host": host}
    if probe:
        refresh = getattr(client, "refresh", None)
        if callable(refresh):
            refresh()  # re-probe so "start Ollama later, reload" turns green
    try:
        available = bool(client.available)
    except Exception:  # pragma: no cover - the property already guards itself
        available = False
    # The model list is cache-only (populated by the last probe / the WS hello),
    # so this never triggers a network round-trip on the REST path.
    models = getattr(client, "list_cached_models", lambda: [])()
    return {"available": available, "installed": True, "model": model,
            "models": models, "host": host}


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

    @router.post("/save")
    async def save(body: dict) -> JSONResponse:
        """Persist the viewer's current settings to config.yaml (the "Save"
        button, M4). Applies the provided fields to the live config and writes
        them to ``$OWNVOICEREADER_CONFIG`` or ``~/.ownvoicereader/config.yaml``."""
        saved = save_settings(
            state,
            voice=body.get("voice"),
            style=body.get("style"),
            llm_model=body.get("llm_model"),
            ollama_host=body.get("ollama_host"),
            local_only=body.get("local_only"),
            playback=body.get("playback"),
        )
        return JSONResponse({"saved": True, **saved})

    return router
