"""Frozen desktop entry point (PyInstaller): the ONE native binary of the app.

This is a *single* executable for every platform (a real .app on macOS, an AppImage
on Linux, an .exe on Windows) — there is no sibling ``piper`` exe. The binary has
two modes, selected by a sentinel in ``argv``:

  * **GUI mode** (default): open the viewer in its own native window and force
    ``--window``. A PDF path passed as ``argv[1]`` opens it; otherwise the landing
    page shows and the user drags a PDF in. The browser stays a *loud* last-resort
    fallback inside ``launch_window`` for the rare case no window can open — it is
    never the normal path.

  * **Piper mode** (``--__ov_piper``): when the app needs speech it re-runs *itself*
    as ``<this-binary> --__ov_piper --model ... --data-dir ... --output-raw`` and
    pipes the text on stdin. We strip the sentinel and run Piper's own CLI, so the
    same frozen binary doubles as the TTS subprocess — self-contained and offline.
    ``tts_engine.PiperEngine`` builds that invocation when it detects this binary is
    the app (frozen), not an external piper.
"""

import sys

from ownvoicereader.cli import main
from ownvoicereader.voice.tts_engine import PIPER_SENTINEL


def run() -> int:
    argv = list(sys.argv[1:])

    # Piper mode: this binary, re-invoked for TTS. Strip the sentinel so Piper's own
    # argparse never sees it, then run Piper's CLI reading text from stdin (exactly as
    # tts_engine.PiperEngine drives it). No window, no server — just synthesize.
    if PIPER_SENTINEL in argv:
        argv = [a for a in argv if a != PIPER_SENTINEL]
        from piper.__main__ import main as piper_main
        # Piper keys its log line off argv[0]; present it as "piper" for a clean log.
        sys.argv = ["piper", *argv]
        return piper_main()

    # GUI mode (default): the packaged binary's whole point is the native window.
    if "--window" not in argv:
        argv = ["--window", *argv]
    return main(argv)


if __name__ == "__main__":
    sys.exit(run())
