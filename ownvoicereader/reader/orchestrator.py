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
from .sections import (
    Section,
    detect_sections,
    section_for_block,
    _body_font_size,
    is_heading,
)

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
    # M1.5 checkpoint 2: section-aware navigation.
    section_index: int = -1        # which detected section this cue belongs to
    section_label: str = ""        # e.g. "1 Introduction", "Abstract", "References"
    section_kind: str = ""         # title | authors | abstract | section | …
    lead_in: str = ""             # spoken section name when jumping in ("" = none)

    @property
    def meta(self) -> dict:
        d = {"index": self.index, "page": self.page,
             "kind": self.kind.value, "is_header": self.is_header}
        if self.bbox is not None:
            d["bbox"] = self.bbox.to_dict()
        if self.section_index >= 0:
            d["section"] = {
                "index": self.section_index,
                "label": self.section_label,
                "kind": self.section_kind,
            }
        return d


# The top band of a page (PDF points) holds the running header. A short block in
# this band on a *later* page is a repeated venue / author / title header.
_HEADER_Y = 90.0
_MAX_HEADER_CHARS = 120


def _is_running_header(block: Block, text: str, body_fs: float) -> bool:
    """A short block at the very top of a *later* page that is not a genuine
    heading — the repeated running header (venue / author / title), de-duplicated
    by *role & position* rather than exact string, so a *variant* author line is
    caught too. A real heading at the top of a page is kept."""
    if block.page <= 0:
        return False
    if not block.bbox or block.bbox.y0 > _HEADER_Y:
        return False
    if len(text) >= _MAX_HEADER_CHARS:
        return False
    if is_heading(block, text, body_fs):
        return False
    return True


def _lead_in_spoken(lead_in: str, text: str) -> str:
    """Compose the spoken form when jumping into a section.

    If the first cue of the section is a *standalone* heading (short, no ending
    punctuation), the lead-in *replaces* it so we don't hear "Section 3, Method
    3. Method". For a merged heading+body cue we prepend instead."""
    if not lead_in:
        return text
    words = text.split()
    if len(words) <= 10 and not text.rstrip().endswith((".", "!", "?")):
        return lead_in
    return lead_in + " " + text


def _is_page_number_or_stray(text: str) -> bool:
    """True for page numbers and lone markers we should never speak."""
    t = text.strip()
    if len(t) <= 3:
        return True
    return bool(_DIGITS_ONLY.fullmatch(t))


def _split_into_chunks(text: str) -> list[str]:
    """Split prose into TTS-sized sentence-group chunks.

    **No sentence is ever dropped.** When a sentence does not fit the rest of the
    current chunk it becomes the start of the *next* chunk — the remainder is
    carried forward, not discarded (a prior version lost it, which is what made
    whole sentences vanish mid-paragraph during playback). A single over-long
    "sentence" is broken on word boundaries. Consequently the concatenation of the
    returned chunks contains every word of the input — asserted in the test-suite.
    """
    parts = _SENTENCE_SPLIT.split(text.strip())
    chunks: list[str] = []
    cur = ""

    def flush() -> None:
        nonlocal cur
        if cur:
            chunks.append(cur)
        cur = ""

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > _MAX_CHUNK_CHARS:
            # An over-long single "sentence": flush the pending chunk, then break
            # it on word boundaries so no chunk exceeds the limit.
            flush()
            buf = ""
            for w in p.split():
                if buf and len(buf) + 1 + len(w) > _MAX_CHUNK_CHARS:
                    chunks.append(buf)
                    buf = w
                else:
                    buf = w if not buf else buf + " " + w
            cur = buf
            continue
        # A normal sentence: append it to the running chunk if it fits, else start
        # a fresh chunk with it. The sentence is never lost either way.
        if not cur:
            cur = p
        elif len(cur) + 1 + len(p) <= _MAX_CHUNK_CHARS:
            cur = cur + " " + p
        else:
            flush()
            cur = p
    flush()
    return chunks


def _build(doc: ReadingDocument, config=None, scope=None) -> tuple[list[Cue], list[Section]]:
    """Build the ordered, speakable cue list *and* the section list together.

    * Figure / table blocks are skipped for now (M2 explains them).
    * Page numbers, stray markers, and **running headers** (repeated venue /
      author / title lines on later pages) are dropped — the running header is
      de-duplicated by *role*, so a variant author line is caught.
    * Remaining blocks are detected into **sections** (title / authors /
      abstract / numbered sections / …); every cue is stamped with its section,
      and the first cue of an announced section carries a spoken ``lead_in``.

    Returns ``(cues, sections)``; ``build_cues`` is the cues-only wrapper.
    """
    text_blocks = [b for b in doc.blocks if b.kind.value == "text"]
    body_fs = _body_font_size(text_blocks)

    # Filter to the spoken blocks (page numbers, running headers, exact dups).
    spoken: list[tuple[Block, str]] = []
    seen: set[str] = set()
    for b in text_blocks:
        text = " ".join(b.text.split())
        if not text or _is_page_number_or_stray(text):
            continue
        if _is_running_header(b, text, body_fs):
            continue  # repeated running header — spoken once, in the front matter
        norm = re.sub(r"\s+", " ", text).lower()
        if norm in seen:
            continue  # duplicate running header / repeated block
        seen.add(norm)
        spoken.append((b, text))

    sections = detect_sections([b for b, _ in spoken])

    cues: list[Cue] = []
    section_first_cue: dict[int, int] = {}
    idx = 0
    for bi, (b, text) in enumerate(spoken):
        sec = section_for_block(sections, bi)
        is_header = bool(b.bbox and b.bbox.y0 < _HEADER_Y and len(text) < _MAX_HEADER_CHARS)
        is_section_start = sec is not None and bi == sec.block_start
        lead_in = sec.announce_label if (is_section_start and sec and sec.announce_label) else ""
        for chunk in _split_into_chunks(text):
            cues.append(Cue(
                index=idx, text=chunk, block_id=b.id, page=b.page,
                bbox=b.bbox, is_header=is_header,
                section_index=sec.index if sec else -1,
                section_label=sec.label if sec else "",
                section_kind=sec.kind if sec else "",
                lead_in=lead_in,
            ))
            if is_section_start and sec is not None and sec.index not in section_first_cue:
                section_first_cue[sec.index] = idx
            idx += 1

    # Fill each section's cue range. Two passes: first every cue_start, then
    # every cue_end (which is the *next* section's cue_start, or the total).
    # A single pass would read a neighbour's cue_start before it is set.
    for sec in sections:
        sec.cue_start = section_first_cue.get(sec.index, -1)
    for j, sec in enumerate(sections):
        nxt = sections[j + 1] if j + 1 < len(sections) else None
        sec.cue_end = nxt.cue_start if (nxt and nxt.cue_start >= 0) else idx

    log.info("built %d cue(s) over %d section(s) from %d block(s)",
             len(cues), len(sections), len(doc.blocks))
    return cues, sections


def build_cues(doc: ReadingDocument, config=None, scope=None) -> list[Cue]:
    """Build the ordered, speakable cue list (public; returns cues only)."""
    return _build(doc, config, scope)[0]


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
        self.cues, self.sections = _build(doc, config, scope)
        self.n = len(self.cues)
        self.pos = 0
        self.state = "idle"  # idle | playing | paused | stopped
        self._style = get_style(config.style if config else None)
        self._voice = config.voice if config else None
        self._last: Optional[tuple[Cue, Optional[bytes], ReadingEvent]] = None
        # M1.5 checkpoint 2: speak a section's name when the reader jumps in.
        # On by default (the user's choice); a later milestone can make it a
        # per-style knob.
        self.announce = True
        self._announce_next = False

    # --- introspection --------------------------------------------------- #
    @property
    def done(self) -> bool:
        return self.pos >= self.n

    def status(self) -> dict:
        sec = self.section_at(self.pos)
        return {
            "state": self.state,
            "pos": self.pos,
            "n": self.n,
            "engine": self.tts.name if self.tts else None,
            "voice": self._voice,
            "style": self._style.name,
            "styles": style_names(),
            "current_page": self.cues[self.pos].page if 0 <= self.pos < self.n else None,
            "section": sec.to_dict() if sec else None,
        }

    # --- section navigation (M1.5 checkpoint 2) -------------------------- #
    def section_at(self, pos: int) -> Optional[Section]:
        """The section whose cue range contains ``pos`` (last one started at or
        before ``pos``). ``None`` if there are no sections."""
        cur: Optional[Section] = None
        for sec in self.sections:
            if sec.cue_start < 0:
                continue
            if sec.cue_start <= pos:
                cur = sec
            else:
                break
        return cur

    def next_section_start(self, pos: int) -> int:
        """Cue-index of the section *after* the one at ``pos`` (a forward jump).
        Returns ``pos`` (no-op) if we are already at the last section."""
        cur = self.section_at(pos)
        cur_idx = cur.index if cur else -1
        for sec in self.sections:
            if sec.index > cur_idx and sec.cue_start >= 0:
                return sec.cue_start
        return pos

    def prev_section_start(self, pos: int) -> int:
        """Cue-index to roll back to: the *start of the current section* if we are
        past it (re-hear the section), else the *previous* section's start."""
        cur = self.section_at(pos)
        if cur is None:
            return 0
        if pos > cur.cue_start:
            return cur.cue_start
        cur_idx = cur.index
        prev: Optional[Section] = None
        for sec in self.sections:
            if sec.index < cur_idx:
                prev = sec
            else:
                break
        return prev.cue_start if prev else 0

    def prev_section_skip_start(self, pos: int) -> int:
        """Cue-index to *skip back* to: the start of the section *before* the one
        containing ``pos`` — i.e. skip the whole current section. Unlike
        :meth:`prev_section_start` (which, mid-section, returns the current
        section's *start* so you can re-hear it), this always lands on the
        *previous* section's start, so a double-click jumps back a full section.
        Clamps to 0 when there is no previous section."""
        cur = self.section_at(pos)
        if cur is None:
            return 0
        prev: Optional[Section] = None
        for sec in self.sections:
            if sec.index < cur.index:
                prev = sec
            else:
                break
        return prev.cue_start if (prev and prev.cue_start >= 0) else 0

    def _jump_to(self, target: int) -> int:
        """Set the playhead to ``target`` and flag the section name to be spoken
        on the next cue (the rollback / skip lead-in)."""
        target = max(0, min(target, self.n))
        self.pos = target
        sec = self.section_at(target)
        self._announce_next = bool(sec and sec.cue_start == target and sec.announce_label)
        return self.pos

    def next_section(self) -> int:
        """Skip forward to the start of the next section (announces its name)."""
        return self._jump_to(self.next_section_start(self.pos))

    def prev_section(self) -> int:
        """Roll back to the start of the current / previous section."""
        return self._jump_to(self.prev_section_start(self.pos))

    def prev_section_skip(self) -> int:
        """Skip back a *whole* section: land at the start of the section *before*
        the current one (a double-click on ⏮).

        Unlike :meth:`prev_section` — which, mid-section, re-hears the current
        section's *start* — this skips the section you are in and lands on the
        *previous* section's start. A section name is announced if we land on a
        section start (``_jump_to`` handles that)."""
        return self._jump_to(self.prev_section_skip_start(self.pos))

    def next_cue(self) -> int:
        """Skip forward exactly one cue (a ~paragraph).

        The fine complement to :meth:`next_section`: instead of snapping to a
        section boundary, it advances the playhead by a single cue and announces
        a section name only if the new position happens to land on a section
        start (``_jump_to`` already handles that).
        """
        return self._jump_to(self.pos + 1)

    def prev_cue(self) -> int:
        """Roll back exactly one cue (a ~paragraph).

        Unlike :meth:`prev_section` (which, mid-section, snaps to the current
        section's *start*), this always moves back by a single cue, so from the
        start of a cue it re-hears the previous paragraph rather than jumping to
        the section start. A section name is announced if we land on a section
        start.
        """
        return self._jump_to(self.pos - 1)

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
    def _event_for(self, cue: Cue, spoken: Optional[str] = None) -> ReadingEvent:
        return ReadingEvent(
            type=ReadingEventType.TEXT,
            text=spoken if spoken is not None else cue.text,
            block_id=cue.block_id,
            page=cue.page,
            meta=cue.meta,
        )

    async def emit_next(self) -> Optional[tuple[ReadingEvent, Optional[bytes]]]:
        """Synthesize the cue at ``pos`` and return (event, wav_bytes).

        Returns ``None`` when the reading is finished. On a TTS failure the
        cue is skipped with a NOTICE event instead of crashing (M6 path).

        When the reader jumped into this cue (``_announce_next``) and the cue
        carries a section ``lead_in``, the spoken form leads with the section
        name ("Section 3, Method") so the listener knows where they landed.
        """
        if self.pos >= self.n:
            return None
        cue = self.cues[self.pos]
        self.pos += 1
        # Section lead-in (M1.5): spoken only when we jumped into this cue.
        spoken = cue.text
        if self.announce and self._announce_next and cue.lead_in:
            spoken = _lead_in_spoken(cue.lead_in, cue.text)
        self._announce_next = False
        event = self._event_for(cue, spoken=spoken)
        audio: Optional[bytes] = None
        if self.tts is not None:
            try:
                audio = await self.tts.synthesize(spoken, self._style, self._voice)
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
