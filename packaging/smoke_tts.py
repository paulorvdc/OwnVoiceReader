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
    """Find a bundled voice .onnx under either the onedir or the .app layout."""
    candidates = [
        onnx_root / "_internal" / "voice" / "models",          # PyInstaller 6.x onedir
        onnx_root,                                              # a flat layout
        onnx_root / "Contents" / "Resources" / "_internal" / "voice" / "models",  # .app
    ]
    for base in candidates:
        found = next(base.glob("*.onnx"), None) if base.is_dir() else None
        if found:
            return found
    for found in onnx_root.rglob("*.onnx"):
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

    out = Path(tempfile.mkdtemp(prefix="ov_smoke_"))
    text = "Hello. This is the bundled Piper voice, reading a paper."
    # The same invocation the app uses: the binary in Piper mode, text on stdin, a WAV
    # written to --output-dir. The sentinel makes this one binary run Piper's CLI.
    proc = subprocess.run(
        [str(binary), PIPER_SENTINEL, "-m", str(onnx), "--output-dir", str(out)],
        input=text.encode("utf-8"),
        timeout=180,
    )
    if proc.returncode != 0:
        print(f"FAIL: Piper mode exited {proc.returncode}:\n{proc.stderr.decode()[-400:]}",
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
