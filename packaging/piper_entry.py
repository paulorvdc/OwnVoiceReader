"""Frozen entry for the bundled ``piper`` TTS subprocess.

The reader drives Piper as a *subprocess* (``tts_engine.locate_piper_binary`` finds
this executable beside the app, at ``sys.executable.parent / "piper"``). It just runs
Piper's own CLI, so the binary is fully self-contained and offline — no separate Piper
install on the user's machine. Built as a second EXE in the same one-dir bundle.
"""

import sys

from piper.__main__ import main


if __name__ == "__main__":
    # Piper's CLI keys off sys.argv[0]; present it as "piper" for a clean log line.
    sys.argv[0] = "piper"
    sys.exit(main())
