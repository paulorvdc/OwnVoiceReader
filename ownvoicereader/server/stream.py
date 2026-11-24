"""server/stream.py — WebSocket transport for reading events + audio.

M0 ships a minimal, working `/ws` so the transport path can be tested early:
the browser connects and receives a `hello` frame; anything it sends is echoed
back. In M1 this becomes the real reading stream — the orchestrator pushes
`text` / `explain` / `pause` / `section` events and framed TTS audio, and the
browser pushes control commands (play/pause/stop/skip/replay).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("ownvoicereader.stream")


def build_stream_router(state) -> APIRouter:
    router = APIRouter(tags=["stream"])

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        log.info("viewer connected over /ws")
        try:
            # M0 handshake. The real event/audio framing arrives in M1.
            await websocket.send_json(
                {"type": "hello", "msg": "ownvoicereader transport (M0 — text reader arrives in M1)",
                 "m1_events": ["text", "explain", "pause", "resume", "stop", "section", "notice"]}
            )
            while True:
                data = await websocket.receive_text()
                # Echo for now; M1 replaces this with control-command handling.
                await websocket.send_json({"type": "echo", "data": data})
        except WebSocketDisconnect:
            log.info("viewer disconnected")
        except Exception:  # pragma: no cover
            log.exception("ws loop error")

    return router
