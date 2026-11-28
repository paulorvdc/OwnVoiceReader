#!/usr/bin/env python3
"""packaging/smoke_tts.py — prove the *bundled* Piper TTS works, cross-platform.

After the PyInstaller build, the one-dir bundle contains the ``piper`` exe and the
bundled voice. This runs the bundled piper exactly as the app drives it
(tts_engine.PiperEngine feeds text on stdin and reads a WAV from --output-dir), then
asserts a non-empty WAV appears. It's the end-to-end check that the self-contained
binary can synthesize speech offline. CI runs it on every platform after the build.

Usage:  python packaging/smoke_tts.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ONEDIR = REPO / "dist" / "OwnVoiceReader"


def main() -> int:
    is_win = os.name == "nt"
    piper = ONEDIR / ("piper.exe" if is_win else "piper")
    if not piper.exists():
        print(f"FAIL: bundled piper not found at {piper}", file=sys.stderr)
        return 1
    # PyInstaller 6.x one-dir: the voice is bundled under _internal/voice/models.
    models = ONEDIR / "_internal" / "voice" / "models"
    onnx = next(models.glob("*.onnx"), None)
    if onnx is None:
        print(f"FAIL: no voice model bundled under {models}", file=sys.stderr)
        return 1

    out = Path(tempfile.mkdtemp(prefix="ov_smoke_"))
    text = "Hello. This is the bundled Piper voice, reading a paper."
    # The same invocation the app uses: text on stdin, WAV written to --output-dir.
    proc = subprocess.run(
        [str(piper), "-m", str(onnx), "--output-dir", str(out)],
        input=text.encode("utf-8"),
        timeout=180,
    )
    if proc.returncode != 0:
        print(f"FAIL: piper exited {proc.returncode}", file=sys.stderr)
        return 1
    wavs = [p for p in out.glob("*.wav") if p.stat().st_size > 0]
    if not wavs:
        print("FAIL: piper ran but produced no non-empty WAV", file=sys.stderr)
        return 1
    wav = wavs[0]
    print(
        f"OK: {piper.name} synthesized {wav.name} "
        f"({wav.stat().st_size} bytes) from the bundled voice "
        f"— self-contained TTS works."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
