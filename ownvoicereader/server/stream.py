"""server/stream.py — M1: the reading transport (WebSocket).

This is how the viewer hears the paper. One WebSocket per browser tab carries
the whole reading:

Server -> browser  (text JSON frames, then a binary WAV frame per cue)
    {type:"hello",  doc, voice, cue_count}
    {type:"event",  event:{ReadingEvent}}     # transcript + highlight/scroll
    {type:"audio",  cue_index, sample_rate, length}   # "a WAV frame follows"
    <BINARY>                                   # the WAV bytes for that cue
    {type:"end"}                               # reading finished
    {type:"state",  ...}                       # after a control action (UI sync)
    {type:"notice"/"error", text}              # engine-level messages

Browser -> server  (text JSON frames)
    {cmd:"start"}    begin/continue from the current position (state-only)
    {cmd:"resume"}   resume after a pause (state-only — produces nothing)
    {cmd:"continue"} pull the *next* cue — the **sole** forward producer
    {cmd:"pause"}    pause playback (the browser also pauses its <audio>)
    {cmd:"stop"}     stop and reset to the start
    (skip / replay are handled client-side in transport.js, losslessly)
    {cmd:"set_style", style} / {cmd:"set_voice", voice}   live switch (M4)
    {cmd:"status"}   request the current playback status

The :class:`Orchestrator` is created per connection over the shared document,
so the playhead / playback state is independent per tab while the text (and the
TTS engine) is shared. Audio is streamed as self-describing WAV frames so a
single HTML5 ``<audio>`` element can play them with instant pause/stop (A9).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..model import ReadingEvent, ReadingEventType
from ..reader.orchestrator import Orchestrator
from .api import get_tts_engine

log = logging.getLogger("ownvoicereader.stream")


async def _send_event(ws: WebSocket, event: ReadingEvent) -> None:
    """Send a reading event (transcript + highlight info) as JSON."""
    await ws.send_json({"type": "event", "event": event.to_dict()})


async def _produce_and_send(ws: WebSocket, orch: Orchestrator) -> None:
    """Synthesize the next cue and stream its event + WAV (if any)."""
    result = await orch.emit_next()
    if result is None:
        await ws.send_json({"type": "end"})
        await ws.send_json({"type": "state", **orch.status()})
        return

    event, audio = result
    await _send_event(ws, event)
    if audio is not None and orch.tts is not None:
        idx = event.meta.get("index", 0)
        await ws.send_json({
            "type": "audio",
            "cue_index": idx,
            "block_id": event.block_id,
            "page": event.page,
            "sample_rate": getattr(orch.tts, "sample_rate", 22050),
            "length": len(audio),
        })
        await ws.send_bytes(audio)


def _make_orchestrator(state) -> Orchestrator:
    """Build a per-connection orchestrator over the shared document + TTS."""
    tts = get_tts_engine(state)
    doc = state.document
    if doc is None:
        # No document loaded yet: a zero-cue orchestrator so the viewer can still
        # connect and get a hello.
        from ..model import ReadingDocument

        doc = ReadingDocument()
    return Orchestrator(doc, tts, state.config)


def build_stream_router(state) -> APIRouter:
    router = APIRouter(tags=["stream"])

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        log.info("viewer connected over /ws")
        orch: Orchestrator | None = None
        try:
            # Handshake: what document / voice / how many cues we have.
            orch = _make_orchestrator(state)
            tts = get_tts_engine(state)
            from .api import _voice_info

            await websocket.send_json({
                "type": "hello",
                "doc": state.document.to_dict() if state.document else None,
                "voice": _voice_info(state),
                "cue_count": orch.n,
                "state": orch.status(),
            })
            if state.document is None:
                await websocket.send_json({
                    "type": "notice",
                    "text": "no document loaded — load a PDF, then press play",
                })

            while True:
                data = await websocket.receive_text()
                msg = _parse(data)
                if msg is None:
                    continue
                cmd = msg.get("cmd", "")
                if orch is None:
                    orch = _make_orchestrator(state)

                if cmd == "start" or cmd == "resume":
                    # State-only: the viewer pulls each cue with `continue`, so a
                    # resume never synthesizes a cue the client may already hold as
                    # its lookahead. This is what makes pause/resume *lossless* —
                    # the server's playhead is the single source of truth, and only
                    # `continue` advances it.
                    orch.resume()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "continue":
                    # The sole forward producer: synthesize the cue at the current
                    # playhead and advance past it. Returns `end` when finished.
                    if orch.done:
                        await websocket.send_json({"type": "end"})
                    else:
                        await _produce_and_send(websocket, orch)
                elif cmd == "pause":
                    orch.pause()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "stop":
                    orch.stop()
                    await websocket.send_json({"type": "state", **orch.status()})
                # `skip` and `replay` are handled client-side in transport.js so they
                # can be lossless and predictable: the client owns its lookahead
                # queue and re-plays a kept blob, instead of the server advancing
                # past a cue it produced-but-the-client-has-not-heard.
                elif cmd == "set_style":
                    name = orch.set_style(msg.get("style", ""))
                    st = orch.status()
                    st["style"] = name
                    await websocket.send_json({"type": "state", **st})
                elif cmd == "set_voice":
                    v = orch.set_voice(msg.get("voice"))
                    st = orch.status()
                    st["voice"] = v
                    await websocket.send_json({"type": "state", **st})
                elif cmd == "status":
                    await websocket.send_json({"type": "state", **orch.status()})
                else:
                    log.debug("unknown ws command: %s", cmd)
        except WebSocketDisconnect:
            log.info("viewer disconnected")
        except Exception:  # pragma: no cover
            log.exception("ws loop error")
            try:
                await websocket.send_json({"type": "error", "text": "stream error"})
            except Exception:
                pass

    return router


def _parse(data: str):
    """Best-effort JSON parse of an inbound control frame."""
    import json

    try:
        return json.loads(data)
    except Exception:
        log.debug("could not parse ws frame: %r", data[:120])
        return None
