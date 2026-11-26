"""server/stream.py — M1: the reading transport (WebSocket).

This is how the viewer hears the paper. One WebSocket per browser tab carries
the whole reading:

Server -> browser  (text JSON frames, then a binary WAV frame per cue)
    {type:"hello",  doc, voice, vision, cue_count}
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
    {cmd:"next_section"} jump forward to the start of the next section
    {cmd:"prev_section"} roll back to the start of the current/previous section
    {cmd:"prev_section_skip"} skip back a *whole* section (start of the previous one)
    {cmd:"next_cue"}     skip forward exactly one cue (~paragraph)
    {cmd:"prev_cue"}     roll back exactly one cue (~paragraph)
    (a lossless client-side skip/replay remains as a fallback in transport.js)
    {cmd:"set_style", style} / {cmd:"set_voice", voice} / {cmd:"set_model", llm_model}
         / {cmd:"set_local_only", local_only}   live switch of the reading settings
         (M4); a `state` reply carries the new value back so the UI can resync.
     {cmd:"set_ollama_host", host}  point the shared vision client at a different
         Ollama server (M4: the "Refresh models" button); a `vision` reply carries
         the fresh {available, model, models, host} so the picker + badge refresh.
    {cmd:"status"}   request the current playback status

    A `state` frame now also carries the current `section` ({index,label,kind,
    cue_start,cue_end,announce}) so the viewer can update its "now reading"
    indicator and its cue counter on a section jump.

The :class:`Orchestrator` is created per connection over the shared document,
so the playhead / playback state is independent per tab while the text (and the
TTS engine) is shared. Audio is streamed as self-describing WAV frames so a
single HTML5 ``<audio>`` element can play them with instant pause/stop (A9).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..model import ReadingEvent, ReadingEventType
from ..reader.orchestrator import Orchestrator
from .api import get_tts_engine, get_llm_client

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
    """Build a per-connection orchestrator over the shared document + TTS, with
    the shared vision client (M2.5) so figure/table explanations are enhanced in
    the background when a vision model is available."""
    tts = get_tts_engine(state)
    doc = state.document
    if doc is None:
        # No document loaded yet: a zero-cue orchestrator so the viewer can still
        # connect and get a hello.
        from ..model import ReadingDocument

        doc = ReadingDocument()
    return Orchestrator(doc, tts, state.config, llm=get_llm_client(state))


def build_stream_router(state) -> APIRouter:
    router = APIRouter(tags=["stream"])

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        log.info("viewer connected over /ws")
        orch: Orchestrator | None = None
        try:
            # Handshake: what document / voice / vision / how many cues we have.
            orch = _make_orchestrator(state)
            tts = get_tts_engine(state)
            from .api import _voice_info, _vision_info

            # The vision badge is *probed* (a fresh reachability check, run in a
            # thread so the event loop never blocks on a slow/absent Ollama); the
            # REST path stays cache-only. One cached round-trip, shared with the
            # orchestrator's background thread.
            vision = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _vision_info(state, probe=True))

            await websocket.send_json({
                "type": "hello",
                "doc": state.document.to_dict() if state.document else None,
                "voice": _voice_info(state),
                "vision": vision,
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
                elif cmd == "next_section":
                    # M1.5 checkpoint 2: jump forward to the start of the next
                    # section (announces its name on the next cue). The playhead
                    # is moved server-side; the state frame carries the new
                    # section so the viewer can resync its counter + indicator.
                    orch.next_section()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "prev_section":
                    # Roll back to the start of the current / previous section.
                    orch.prev_section()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "prev_section_skip":
                    # Double-click on ⏮: skip back a *whole* section — land at the
                    # start of the section *before* the current one, instead of
                    # re-hearing the current section's start. Announces the section
                    # name on landing (the state frame carries it for the chip).
                    orch.prev_section_skip()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "next_cue":
                    # M1.5: fine forward jump — advance exactly one cue (a
                    # ~paragraph). Announces a section name if it lands on a
                    # section start. The playhead moves server-side; the state
                    # frame resyncs the viewer's counter + section chip.
                    orch.next_cue()
                    await websocket.send_json({"type": "state", **orch.status()})
                elif cmd == "prev_cue":
                    # M1.5: fine rollback — go back exactly one cue instead of
                    # snapping to the current section's start.
                    orch.prev_cue()
                    await websocket.send_json({"type": "state", **orch.status()})
                # `skip` and `replay` remain available as lossless client-side
                # per-cue helpers in transport.js, but the buttons now use the
                # server-side fine/coarse commands above so the counter + section
                # chip resync from a single `state` frame.
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
                elif cmd == "set_model":
                    # M4: switch the vision/LLM model live. `orch.set_model`
                    # updates the *shared* client + config, so the choice applies to
                    # the next document load and is persisted by "Save". (Vision for
                    # the document already open is pre-computed in the background, so
                    # the new model fully takes effect on the next load.)
                    m = orch.set_model(msg.get("llm_model") or msg.get("model"))
                    st = orch.status()
                    st["llm_model"] = m
                    await websocket.send_json({"type": "state", **st})
                elif cmd == "set_local_only":
                    # M4: toggle the no-cloud-egress setting live; "Save" persists it.
                    lo = orch.set_local_only(msg.get("local_only", True))
                    st = orch.status()
                    st["local_only"] = lo
                    await websocket.send_json({"type": "state", **st})
                elif cmd == "set_ollama_host":
                    # M4: point the shared vision client at a *different* Ollama server
                    # (the "Refresh models" button). Updates the client + config, then
                    # re-probes so the model picker + badge refresh *now* — no page
                    # reload. The reply carries the fresh vision info + the host the
                    # reader is now pointing at, for the UI.
                    orch.set_ollama_host(msg.get("host"))
                    from .api import _vision_info
                    vision = _vision_info(state, probe=True)
                    await websocket.send_json({
                        "type": "vision",
                        "vision": vision,
                        "host": vision.get("host"),
                    })
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
