#!/usr/bin/env python3
"""packaging/smoke_tts.py — prove the *bundled* Piper TTS works, cross-platform.

After the PyInstaller build the bundle contains a single native executable that runs
Piper's own CLI in its Piper mode (``--__ov_piper``). This runs that binary exactly
as the app drives it (tts_engine.PiperEngine feeds text on stdin, Piper writes a WAV
to --output-dir), then asserts a non-empty WAV appears. It's the end-to-end check
that the self-contained binary can synthesize speech offline. CI runs it on every
OS after the build.

Usage:  python packaging/smoke_tts.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"
APP = "OwnVoiceReader"
# The sentinel that turns the frozen binary into a Piper subprocess (see
# ownvoicereader.voice.tts_engine.PIPER_SENTINEL / packaging/ownvoicereader_entry.py).
PIPER_SENTINEL = "--__ov_piper"


def _main_binary() -> Path | None:
    """Locate the single native executable, per platform (a .app on macOS)."""
    is_win = os.name == "nt"
    # Linux / Windows: a one-dir bundle -> dist/OwnVoiceReader/OwnVoiceReader[.exe].
    onedir = DIST / APP / (f"{APP}.exe" if is_win else APP)
    if onedir.exists():
        return onedir
    # macOS: a .app -> dist/OwnVoiceReader.app/Contents/MacOS/OwnVoiceReader.
    if sys.platform == "darwin":
        app_bin = DIST / f"{APP}.app" / "Contents" / "MacOS" / APP
        if app_bin.exists():
            return app_bin
    # Fall back: any matching executable under dist/.
    for cand in DIST.rglob(f"{APP}.exe" if is_win else APP):
        if cand.is_file() and cand.name in (APP, f"{APP}.exe"):
            return cand
    return None


def _bundled_voice(onnx_root: Path) -> Path | None:
    """Find the bundled Piper voice .onnx under the onedir or .app layout.

    The frozen bundle also carries *other* .onnx files — PyMuPDF's onnx layout
    models under ``pymupdf/layout/resources/onnx/`` — so a blind ``rglob`` could hand
    Piper one of *those* (it then looks for a voice config sidecar the model doesn't
    have and crashes with FileNotFoundError). On a fresh CI runner the PyMuPDF model
    sorts ahead of the voice in path order, so the fallback must never grab it. We
    check the known voice-model dirs first, and any rglob fallback must resolve to a
    file under a ``voice/models`` dir so a third-party onnx is never mistaken for the
    voice.
    """
    candidates = [
        onnx_root / APP / "_internal" / "voice" / "models",                              # onedir (Linux/Windows)
        onnx_root / f"{APP}.app" / "Contents" / "Resources" / "_internal" / "voice" / "models",  # .app (macOS)
        onnx_root / "_internal" / "voice" / "models",                                    # a flat _MEIPASS / one-file
        onnx_root / "voice" / "models",                                                  # a flat layout
    ]
    for base in candidates:
        if base.is_dir():
            found = next(base.glob("*.onnx"), None)
            if found:
                return found
    for found in onnx_root.rglob("*.onnx"):
        parts = found.relative_to(onnx_root).parts
        if "voice" in parts and "models" in parts:  # never a third-party (e.g. PyMuPDF) onnx
            return found
    return None


def main() -> int:
    binary = _main_binary()
    if binary is None:
        print(f"FAIL: the {APP} executable was not found under {DIST}", file=sys.stderr)
        return 1

    onnx = _bundled_voice(DIST)
    if onnx is None:
        print(f"FAIL: no voice model found under {DIST}", file=sys.stderr)
        return 1

    print(f"running Piper mode: {binary}  with voice {onnx}")
    out = Path(tempfile.mkdtemp(prefix="ov_smoke_"))
    text = "Hello. This is the bundled Piper voice, reading a paper."
    # The same invocation the app uses: the binary in Piper mode, text on stdin, a WAV
    # written to --output-dir. The sentinel makes this one binary run Piper's CLI.
    cmd = [str(binary), PIPER_SENTINEL, "-m", str(onnx), "--output-dir", str(out)]

    # A Popen (not subprocess.run) so a *hang* is diagnosable: a run that never exits
    # — a frozen GUI build that lingers after Piper's exception, or a model that won't
    # load — is killed and its captured stderr printed, instead of a bare "timed out"
    # with no signal (the failure mode CI hit on Windows before the rglob fix).
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        _stdout, err = proc.communicate(input=text.encode("utf-8"), timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        _stdout, err = proc.communicate()  # reap the killed child
        tail = (err or b"").decode(errors="replace")[-800:]
        print(f"FAIL: Piper mode hung >180s (no WAV produced). stderr tail:\n{tail}",
              file=sys.stderr)
        return 1

    if proc.returncode != 0:
        err = (err or b"").decode(errors="replace")
        print(f"FAIL: Piper mode exited {proc.returncode}:\n{err[-400:]}",
              file=sys.stderr)
        return 1
    wavs = [p for p in out.glob("*.wav") if p.stat().st_size > 0]
    if not wavs:
        print("FAIL: Piper mode ran but produced no non-empty WAV", file=sys.stderr)
        return 1
    wav = wavs[0]
    print(
        f"OK: {binary.name} (Piper mode) synthesized {wav.name} "
        f"({wav.stat().st_size} bytes) from the bundled voice — self-contained TTS works."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
