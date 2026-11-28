"""Frozen desktop entry point (PyInstaller): always open the viewer in its own window.

The packaged binary's whole point is the native window (Discord-like, not a browser
tab), so we force ``--window``. A PDF path passed as ``argv[1]`` opens it; otherwise
the landing page shows and the user drags a PDF in. The browser stays a *loud*
last-resort fallback inside ``launch_window`` for the rare case no window can open
(no display / no backend) — it is never the normal path.

This is the ``console=False`` entry for the GUI build; a ``console=True`` twin is
kept for ``--auto``/debug (see ``ownvoicereader.spec``).
"""

import sys

from ownvoicereader.cli import main


def run() -> int:
    argv = list(sys.argv[1:])
    # Force the window path unless the user already passed --window. Never --no-browser:
    # the binary's identity is the window.
    if "--window" not in argv:
        argv = ["--window", *argv]
    return main(argv)


if __name__ == "__main__":
    sys.exit(run())
