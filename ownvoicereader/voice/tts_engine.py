"""voice/tts_engine.py — M1: text-to-speech, engine-agnostic.

The reader never talks to a specific synthesizer directly; it asks a
:class:`TtsEngine` for WAV audio for a chunk of text, in a given
:class:`Style`. This keeps voice/style switching (M4) and the "no cloud TTS"
decision (A1) clean: only local engines ship in core.

Engines
-------
* :class:`PiperEngine` (primary, A1): the ``piper`` CLI driven by a voice model
  (.onnx). Natural-sounding, fully local. A voice model is downloaded to a
  cache dir on first use (see :func:`locate_voice_dir`).
* :class:`EspeakEngine` (fallback): the ``espeak-ng`` binary, if present — a
  robotic but zero-model smoke-test voice.

Both return **WAV bytes** so the browser can play them with a plain HTML5
``<audio>`` element (A9). Engines that cannot run report ``available = False``
instead of raising, so the orchestrator can degrade gracefully and show a
notice.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .styles import Style, get_style

log = logging.getLogger("ownvoicereader.voice.tts")

# Where Piper voice models are looked up / stored.
VOICE_DIR_ENV = "OWNVOICEREADER_VOICE_DIR"
DEFAULT_VOICE_DIR = Path.home() / ".cache" / "ownvoicereader" / "voices"
# Default voice model (a clear, natural English voice; ~60 MB, downloaded once).
DEFAULT_VOICE = "en_US-lessac-medium"


# --------------------------------------------------------------------------- #
# Audio helpers
# --------------------------------------------------------------------------- #
def wrap_pcm16_le(pcm: bytes, sample_rate: int, channels: int = 1,
                  bits: int = 16) -> bytes:
    """Wrap raw little-endian PCM16 in a minimal (canonical) WAV container."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", len(pcm),
    )
    return header + pcm


# --------------------------------------------------------------------------- #
# Engine interface
# --------------------------------------------------------------------------- #
class TtsEngine(ABC):
    """A local text-to-speech engine producing WAV audio."""

    name: str = "abstract"
    # Populated by subclasses: sample rate of the WAV they emit.
    sample_rate: int = 22050
    channels: int = 1

    @property
    @abstractmethod
    def available(self) -> bool:
        """True if this engine can synthesize right now."""

    @abstractmethod
    def _synthesize_sync(self, text: str, style: Style, voice: Optional[str]) -> bytes:
        """Blocking synthesis → WAV bytes. Runs in an executor."""

    def list_voices(self) -> list[str]:
        return []

    async def synthesize(self, text: str, style: Optional[Style] = None,
                         voice: Optional[str] = None) -> bytes:
        """Synthesize ``text`` (in a thread) and return WAV bytes."""
        style = get_style(style.name if isinstance(style, Style) else style)
        loop = __import__("asyncio").get_running_loop()
        return await loop.run_in_executor(
            None, self._synthesize_sync, text, style, voice
        )


# --------------------------------------------------------------------------- #
# Piper
# --------------------------------------------------------------------------- #
class PiperEngine(TtsEngine):
    """Drive the ``piper`` CLI with an ONNX voice model. Fully local (A1)."""

    name = "piper"

    def __init__(self, binary: str, voice_dir: Path, voice: Optional[str] = None):
        self.binary = binary
        self.voice_dir = Path(voice_dir)
        self._voice = voice or DEFAULT_VOICE
        self.sample_rate = self._read_sample_rate() or 22050

    @property
    def available(self) -> bool:
        return bool(shutil.which(self.binary) or _bin_exists(self.binary)) \
            and bool(self._model_path())

    def _model_path(self) -> Optional[Path]:
        """Find the .onnx model file for the current voice in the voice dir."""
        if not self.voice_dir.is_dir():
            return None
        # Exact match first, then prefix match.
        for onnx in sorted(self.voice_dir.glob("*.onnx")):
            stem = onnx.stem  # e.g. en_US-lessac-medium
            if stem == self._voice or self._voice in stem:
                return onnx
        # Fall back to any model present.
        any_onnx = sorted(self.voice_dir.glob("*.onnx"))
        return any_onnx[0] if any_onnx else None

    def _read_sample_rate(self) -> Optional[int]:
        model = self._model_path()
        if not model:
            return None
        cfg = model.with_suffix(model.suffix + ".json")
        try:
            data = json.loads(cfg.read_text())
            return int(data.get("sampling_rate") or 22050)
        except Exception:
            return None

    def list_voices(self) -> list[str]:
        if not self.voice_dir.is_dir():
            return []
        return sorted(p.stem for p in self.voice_dir.glob("*.onnx"))

    def _synthesize_sync(self, text: str, style: Style, voice: Optional[str]) -> bytes:
        if not text.strip():
            return wrap_pcm16_le(b"", self.sample_rate)
        model = self._model_path()
        if not model:
            raise TtsUnavailable(f"no voice model found in {self.voice_dir}")
        cmd = [
            self.binary,
            "--model", str(model),
            "--data-dir", str(self.voice_dir),
            "--output-raw",
        ]
        if style.length_scale is not None:
            cmd += ["--length-scale", f"{style.length_scale}"]
        if style.noise_scale is not None:
            cmd += ["--noise-scale", f"{style.noise_scale}"]
        if style.sentence_silence is not None:
            cmd += ["--sentence-silence", f"{style.sentence_silence}"]
        try:
            proc = subprocess.run(
                cmd, input=text.encode("utf-8"), capture_output=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise TtsUnavailable(f"piper binary not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TtsUnavailable("piper timed out synthesizing a chunk") from exc
        if proc.returncode != 0:
            raise TtsUnavailable(
                f"piper failed (rc={proc.returncode}): {proc.stderr.decode()[:300]}"
            )
        # --output-raw => raw PCM16LE at the model sample rate; wrap in WAV.
        return wrap_pcm16_le(proc.stdout, self.sample_rate, self.channels, 16)


# --------------------------------------------------------------------------- #
# espeak-ng (fallback / smoke test)
# --------------------------------------------------------------------------- #
class EspeakEngine(TtsEngine):
    """Drive the ``espeak-ng`` binary. Zero-model, robotic, local."""

    name = "espeak-ng"
    sample_rate = 22050

    def __init__(self, binary: str, voice: Optional[str] = None):
        self.binary = binary
        self._voice = voice or "en"

    @property
    def available(self) -> bool:
        return bool(shutil.which(self.binary) or _bin_exists(self.binary))

    def _synthesize_sync(self, text: str, style: Style, voice: Optional[str]) -> bytes:
        if not text.strip():
            return b""
        cmd = [self.binary, "-w"]
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out = Path(tmp.name)
        try:
            cmd += [str(out)]
            if style.length_scale:
                # espeak speed: 170 wpm ~ normal; longer length_scale => slower.
                speed = int(round(170 / max(0.5, style.length_scale)))
                cmd += ["-s", str(speed)]
            proc = subprocess.run(cmd, input=text.encode("utf-8"),
                                  capture_output=True, timeout=60)
            if proc.returncode != 0:
                raise TtsUnavailable(
                    f"espeak-ng failed (rc={proc.returncode}): "
                    f"{proc.stderr.decode()[:300]}"
                )
            return out.read_bytes()
        except FileNotFoundError as exc:
            raise TtsUnavailable(f"espeak-ng binary not found: {self.binary}") from exc
        finally:
            out.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Errors + discovery
# --------------------------------------------------------------------------- #
class TtsUnavailable(Exception):
    """The engine could not produce audio (binary/model missing or failed)."""


def _bin_exists(path: str) -> bool:
    p = Path(path).expanduser()
    return p.is_file() and os.access(p, os.X_OK)


def locate_piper_binary() -> Optional[str]:
    """Find the ``piper`` executable (env override, PATH, then the env bin dir)."""
    env_bin = os.environ.get("OWNVOICEREADER_PIPER_BIN")
    if env_bin and _bin_exists(env_bin):
        return env_bin
    found = shutil.which("piper")
    if found:
        return found
    # The console script lives next to the interpreter running the app.
    cand = Path(sys.executable).parent / "piper"
    if _bin_exists(str(cand)):
        return str(cand)
    return None


def locate_voice_dir() -> Path:
    """Resolve the voice-model directory (env override, else the default cache)."""
    env_dir = os.environ.get(VOICE_DIR_ENV)
    if env_dir:
        return Path(env_dir).expanduser()
    return DEFAULT_VOICE_DIR


def _bundled_voice_dir() -> Path:
    """Locate the voice models shipped inside the app (M6 Phase 2).

    In a normal (pip/conda) install they live next to the package
    (``<pkg>/voice/models``). Under a frozen build they're extracted beside the
    executable (one-dir) or into a temp ``_MEIPASS`` dir (one-file) — resolve
    against that, mirroring the viewer dir resolver in ``server/app.py``.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable)))
        cand = base / "voice" / "models"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parent / "models"


def ensure_default_voice(voice_dir: Optional[Path] = None,
                        voice: str = DEFAULT_VOICE,
                        bundled_dir: Optional[Path] = None) -> Optional[Path]:
    """Copy the bundled default voice into the (writable) user voice dir, first run.

    Out-of-the-box voice: the app ships ``en_US-lessac-medium``; this copies it into
    the user's voice cache the first time, so the reader has a natural voice with no
    download and no setup. Best-effort and idempotent: a voice already present
    (matched by name *and* size) is left alone, and a copy that fails (a read-only
    target, …) is logged, never fatal — "degrade, never abort." ``OWNVOICEREADER_VOICE_DIR``
    still overrides the target. Returns the model path it ensured, or ``None`` if
    there is no bundled voice to copy (e.g. a bare pip install that omits the model).
    """
    src_dir = Path(bundled_dir) if bundled_dir else _bundled_voice_dir()
    onnx_src = src_dir / f"{voice}.onnx"
    if not onnx_src.is_file():
        return None  # no bundled voice -> nothing to ensure
    dst_dir = Path(voice_dir) if voice_dir else locate_voice_dir()
    onnx_dst = dst_dir / onnx_src.name
    # Idempotent: an identical-size copy already present means we're done.
    if onnx_dst.is_file() and onnx_dst.stat().st_size == onnx_src.stat().st_size:
        return onnx_dst
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(onnx_src, onnx_dst)
        cfg_src = src_dir / f"{voice}.onnx.json"
        if cfg_src.is_file():
            shutil.copy2(cfg_src, dst_dir / f"{voice}.onnx.json")
    except OSError as exc:
        log.warning("could not copy the bundled voice to %s: %s", dst_dir, exc)
        return None
    log.info("ensured default voice on first run: %s -> %s", onnx_src.name, onnx_dst)
    return onnx_dst


def build_tts_engine(config=None, voice: Optional[str] = None,
                     voice_dir: Optional[Path] = None) -> Optional[TtsEngine]:
    """Pick the best available local engine.

    Prefers Piper (a natural voice) when a model is present; falls back to
    espeak-ng; returns ``None`` when nothing local can run (the orchestrator
    then shows a "TTS unavailable" notice and reads silently / headless).
    """
    vdir = Path(voice_dir) if voice_dir else locate_voice_dir()
    vname = voice or (config.voice if config else None) or DEFAULT_VOICE

    # Phase 2: make sure the bundled default voice is in the user's voice dir, so a
    # fresh install reads out of the box, offline. A no-op when no voice ships.
    ensure_default_voice(vdir, voice=vname)

    piper_bin = locate_piper_binary()
    if piper_bin:
        engine = PiperEngine(piper_bin, vdir, voice=vname)
        if engine.available:
            log.info("TTS: using Piper (%s, model=%s)", engine.name,
                     engine._model_path().name)
            return engine

    espeak = shutil.which("espeak-ng") or os.environ.get("OWNVOICEREADER_ESPEAK_BIN")
    if espeak:
        engine = EspeakEngine(espeak, voice="en")
        if engine.available:
            log.info("TTS: using espeak-ng (fallback)")
            return engine

    log.warning("TTS: no local engine available (piper/espeak-ng). "
                "Voice output will be unavailable.")
    return None
