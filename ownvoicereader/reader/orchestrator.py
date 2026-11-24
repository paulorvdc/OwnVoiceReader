"""reader/orchestrator.py — M1: the reading engine.

Turns a :class:`ReadingDocument` into a spoken reading. It:

  1. **Builds cues** — splits the reading-ordered text blocks into TTS-sized
     sentence-group chunks, and decides *what is speakable*: it drops page
     numbers and de-duplicates running headers (which repeat on every page) so
     the reader doesn't say "nine zero two four" or repeat the conference line.
  2. **Drives playback** — a small state machine (idle / playing / paused /
     stopped) that the viewer controls with start / pause / resume / stop /
     skip / replay. Each step synthesizes one cue and returns its
     :class:`ReadingEvent` + WAV audio for the transport to stream.

The orchestrator is **transport-agnostic**: the browser path pulls cues from it
over the WebSocket; the headless ``--auto`` path (:meth:`run_to_end`) feeds a
system :class:`~ownvoicereader.voice.player.Player` instead. TTS failures are
degraded to a :data:`NOTICE` event, not a crash (the M6 hardening path).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..model import BBox, Block, ReadingDocument, ReadingEvent, ReadingEventType
from ..voice import get_style, style_names
from ..voice.tts_engine import TtsEngine, TtsUnavailable

log = logging.getLogger("ownvoicereader.reader.orchestrator")

# TTS chunk sizing: a sentence-group cue of roughly this many characters keeps
# each unit short enough to interrupt quickly, yet long enough to be a natural
# phrase for Piper.
_MAX_CHUNK_CHARS = 200
_MIN_CHUNK_CHARS = 40
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# A line that is only a page number / marker (digits, dashes, dots, spaces).
_DIGITS_ONLY = re.compile(r"^[\d\s\.\-]+$")


# --------------------------------------------------------------------------- #
# Cues
# --------------------------------------------------------------------------- #
@dataclass
class Cue:
    """One unit the reader speaks: a chunk of text, with its source location."""

    index: int
    text: str
    block_id: str
    page: int
    bbox: Optional[BBox] = None
    kind: ReadingEventType = ReadingEventType.TEXT
    is_header: bool = False

    @property
    def meta(self) -> dict:
        d = {"index": self.index, "page": self.page,
             "kind": self.kind.value, "is_header": self.is_header}
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        return d


def _is_page_number_or_stray(text: str) -> bool:
    """True for page numbers and lone markers we should never speak."""
    t = text.strip()
    if len(t) <= 3:
        return True
    return bool(_DIGITS_ONLY.fullmatch(t))


def _split_into_chunks(text: str) -> list[str]:
    """Split prose into TTS-sized sentence-group chunks."""
    parts = _SENTENCE_SPLIT.split(text.strip())
    chunks: list[str] = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) <= _MAX_CHUNK_CHARS:
            cur = (cur + " " + p).strip()
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(p) > _MAX_CHUNK_CHARS:
            # A single over-long "sentence": break on words.
            buf = ""
            for w in p.split():
                if len(buf) + len(w) + 1 > _MAX_CHUNK_CHARS:
                    if buf:
                        chunks.append(buf)
                    buf = w
                else:
                    buf = (buf + " " + w).strip()
            cur = buf
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) >= 1]


def build_cues(doc: ReadingDocument, config=None, scope=None) -> list[Cue]:
    """Build the ordered, speakable cue list from a document's blocks.

    * Figure / table blocks are skipped for now (M2 explains them).
    * Page numbers and stray markers are dropped.
    * Running headers that repeat on later pages are de-duplicated (only the
      first occurrence is spoken).
    """
    cues: list[Cue] = []
    seen: set[str] = set()
    idx = 0
    for block in doc.blocks:
        if block.kind.value != "text":
            continue  # figures/tables handled in M2
        text = " ".join(block.text.split())
        if not text or _is_page_number_or_stray(text):
            continue
        norm = re.sub(r"\s+", " ", text).lower()
        if norm in seen:
            continue  # duplicate running header / repeated block
        seen.add(norm)
        # A short block at the very top of a page is a running header.
        is_header = bool(block.bbox and block.bbox.y0 < 90 and len(text) < 120)
        for chunk in _split_into_chunks(text):
            cues.append(Cue(
                index=idx, text=chunk, block_id=block.id, page=block.page,
                bbox=block.bbox, is_header=is_header,
            ))
            idx += 1
    log.info("built %d cue(s) from %d block(s)", len(cues), len(doc.blocks))
    return cues


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class Orchestrator:
    """Playback state machine over a document's cues."""

    def __init__(self, doc: ReadingDocument, tts: Optional[TtsEngine],
                 config=None, scope=None):
        self.doc = doc
        self.tts = tts
        self.config = config
        self.cues = build_cues(doc, config, scope)
        self.n = len(self.cues)
        self.pos = 0
        self.state = "idle"  # idle | playing | paused | stopped
        self._style = get_style(config.style if config else None)
        self._voice = config.voice if config else None
        self._last: Optional[tuple[Cue, Optional[bytes], ReadingEvent]] = None

    # --- introspection --------------------------------------------------- #
    @property
    def done(self) -> bool:
        return self.pos >= self.n

    def status(self) -> dict:
        return {
            "state": self.state,
            "pos": self.pos,
            "n": self.n,
            "engine": self.tts.name if self.tts else None,
            "voice": self._voice,
            "style": self._style.name,
            "styles": style_names(),
            "current_page": self.cues[self.pos].page if 0 <= self.pos < self.n else None,
        }

    # --- live settings (M4) --------------------------------------------- #
    def set_style(self, name: str) -> str:
        self._style = get_style(name)
        return self._style.name

    def set_voice(self, voice: Optional[str]) -> Optional[str]:
        self._voice = voice
        return voice

    # --- controls -------------------------------------------------------- #
    def pause(self) -> None:
        if self.state == "playing":
            self.state = "paused"

    def resume(self) -> None:
        if self.state in ("paused", "idle", "stopped"):
            self.state = "playing"

    def stop(self) -> None:
        self.state = "stopped"
        self.pos = 0
        self._last = None

    def skip(self) -> int:
        """Advance past the current cue; returns the new position."""
        self.pos = min(self.pos + 1, self.n)
        return self.pos

    # --- production ------------------------------------------------------ #
    def _event_for(self, cue: Cue) -> ReadingEvent:
        return ReadingEvent(
            type=ReadingEventType.TEXT,
            text=cue.text,
            block_id=cue.block_id,
            page=cue.page,
            meta=cue.meta,
        )

    async def emit_next(self) -> Optional[tuple[ReadingEvent, Optional[bytes]]]:
        """Synthesize the cue at ``pos`` and return (event, wav_bytes).

        Returns ``None`` when the reading is finished. On a TTS failure the
        cue is skipped with a NOTICE event instead of crashing (M6 path).
        """
        if self.pos >= self.n:
            return None
        cue = self.cues[self.pos]
        self.pos += 1
        event = self._event_for(cue)
        audio: Optional[bytes] = None
        if self.tts is not None:
            try:
                audio = await self.tts.synthesize(cue.text, self._style, self._voice)
            except TtsUnavailable as exc:
                log.warning("TTS unavailable for cue %d: %s", cue.index, exc)
                event = ReadingEvent(
                    type=ReadingEventType.NOTICE,
                    text=f"skipped (voice unavailable: {exc})",
                    block_id=cue.block_id, page=cue.page,
                    meta=cue.meta,
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("TTS error on cue %d", cue.index)
                event = ReadingEvent(type=ReadingEventType.NOTICE,
                                    text=f"skipped (voice error: {exc})",
                                    block_id=cue.block_id, page=cue.page,
                                    meta=cue.meta)
        self._last = (cue, audio, event)
        return event, audio

    async def replay(self) -> Optional[tuple[ReadingEvent, Optional[bytes]]]:
        """Re-synthesize and return the most recently spoken cue."""
        if not self._last:
            return None
        cue, _audio, event = self._last
        audio: Optional[bytes] = None
        if self.tts is not None:
            try:
                audio = await self.tts.synthesize(cue.text, self._style, self._voice)
            except TtsUnavailable as exc:
                audio = None
        return event, audio

    async def run_to_end(self, player) -> int:
        """Headless read to completion (``--auto``). Plays via ``player``.

        Returns the number of cues spoken. Used by the headless / CI path.
        """
        from ..voice.player import NullPlayer

        spoken = 0
        player = player or NullPlayer()
        while not self.done:
            result = await self.emit_next()
            if result is None:
                break
            event, audio = result
            if event.type == ReadingEventType.NOTICE:
                log.warning(event.text)
                continue
            if audio:
                try:
                    player.play(audio)
                except Exception:  # pragma: no cover
                    log.exception("player error")
            spoken += 1
        return spoken
