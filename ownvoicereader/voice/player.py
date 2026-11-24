"""voice/player.py — M1: where the audio actually goes.

Two backends:

* :class:`SystemPlayer` (``--playback system`` / headless ``--auto``): pipes
  WAV to the OS via ``aplay`` (or ``ffplay`` if present). Used for a hands-free
  start-to-finish read without a browser.
* The **browser** backend (the default, A9) lives in the viewer
  (``viewer/transport.js``): the server streams WAV over the WebSocket and a
  single HTML5 ``<audio>`` element plays it, giving instant pause / stop.

A :class:`NullPlayer` is used when no audio output is wanted (e.g. a test or a
"read the transcript only" mode).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

log = logging.getLogger("ownvoicereader.voice.player")


class Player(ABC):
    """Plays a chunk of WAV audio. ``play`` blocks until the chunk finishes."""

    @abstractmethod
    def play(self, wav: bytes, interruptible: bool = False) -> None:
        ...

    def stop(self) -> None:
        """Stop any audio currently playing."""
        return


class NullPlayer(Player):
    """Consumes audio but produces no sound."""

    def play(self, wav: bytes, interruptible: bool = False) -> None:
        return


class SystemPlayer(Player):
    """Play WAV through the OS audio device via ``aplay`` (fallback: ``ffplay``)."""

    def __init__(self, device: str | None = None):
        self._aplay = shutil.which("aplay")
        self._ffplay = shutil.which("ffplay")
        self._device = device
        self._proc: subprocess.Popen | None = None
        if not (self._aplay or self._ffplay):
            log.warning("SystemPlayer: no aplay/ffplay found; audio will be silent")

    def _command(self, wav_path: Path) -> list[str]:
        if self._aplay:
            cmd = [self._aplay, "-q", "-D", "default"]
            if self._device:
                cmd += ["-D", self._device]
            cmd.append(str(wav_path))
            return cmd
        # ffplay fallback (silent device is fine for a headless check).
        return [self._ffplay, "-nodisp", "-autoexit", "-i", str(wav_path)]

    def play(self, wav: bytes, interruptible: bool = False) -> None:
        if not wav or not (self._aplay or self._ffplay):
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav)
            path = Path(tmp.name)
        try:
            self._proc = subprocess.Popen(self._command(path))
            self._proc.wait()
        except Exception:  # pragma: no cover - audio device may be absent
            log.warning("SystemPlayer: failed to play audio", exc_info=True)
        finally:
            self._proc = None
            path.unlink(missing_ok=True)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except Exception:  # pragma: no cover
                self._proc.kill()
        self._proc = None


def build_player(config=None) -> Player:
    """Pick a player from config. Default is the browser (handled in JS); the
    server side only ever needs a system/null player for headless runs."""
    mode = getattr(config, "playback", "browser") if config else "browser"
    if mode == "system":
        return SystemPlayer()
    if mode == "null":
        return NullPlayer()
    # "browser" is the default; the server does not play it, so headless callers
    # fall back to a system player so --auto still makes sound.
    return SystemPlayer()
