"""cli.py — the `ownvoicereader` console entry point (A11).

Usage
-----
    ownvoicereader                         # landing page + drag-drop
    ownvoicereader paper.pdf               # open that PDF in the viewer
    ownvoicereader paper.pdf --auto        # headless read start-to-finish
    ownvoicereader paper.pdf --no-browser  # serve without opening a browser
    ownvoicereader --port 8000             # pin a specific port
    ownvoicereader --port 0                # a random free port (not the default)
    ownvoicereader --local-only            # forbid any cloud egress (default)

The server binds 127.0.0.1 on a fixed default port (6767) unless you pin
another with --port (0 = random). The viewer renders the PDF with PDF.js and
reads it aloud with a local voice; --auto reads start-to-finish headlessly.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ownvoicereader",
        description="your model, your voice, your machine — read a paper aloud, "
                    "with your LLM explaining figures/tables at the right moment.",
    )
    p.add_argument("pdf", nargs="?", help="path to a PDF (omit for the landing page)")
    p.add_argument("--host", default=None, help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=None,
                   help="bind port (default 6767; use 0 for a random free port)")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser automatically")
    p.add_argument("--local-only", action="store_true",
                   help="forbid any cloud egress (this is the default)")
    p.add_argument("--no-cloud", action="store_true", help="alias of --local-only")
    p.add_argument("--playback", choices=["browser", "system", "null"], default=None,
                   help="where audio plays: 'browser' (HTML5, default), "
                        "'system' (headless OS audio), or 'null' (silent)")
    p.add_argument("--auto", action="store_true",
                   help="headless start-to-finish read (full effect in M1; for M0 just serves)")
    p.add_argument("--director", action="store_true",
                   help="let the LLM place figure/table explanations (else: first textual reference)")
    p.add_argument("--llm", dest="llm_model", default=None,
                   help="vision/LLM model id on your local Ollama (e.g. 'lfm3-vl:2b', "
                        "'llama3.2-vision'); default 'lfm3-vl:2b'. Server via OLLAMA_HOST.")
    p.add_argument("--voice", default=None, help="TTS voice id")
    p.add_argument("--style", choices=["academic-calm", "quick-scan", "narrative", "lecture"],
                   default=None, help="reader style preset")
    p.add_argument("--resume", default=None, help="resume from a JSON checkpoint file")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v / -vv for more logging")
    return p


def _configure_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """Console entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    # defaults -> env (OWNVOICEREADER_*) -> CLI flags (final precedence)
    config = Config.from_env()
    if args.pdf:
        config.pdf_path = args.pdf
    config.override_from_args(args)

    # A13: local-only is the default unless the user explicitly opts out.
    # We only flip it off via a future --allow-cloud flag; for now local_only stays True.

    try:
        from .server.app import start
    except Exception as exc:  # pragma: no cover - missing optional dep
        print(f"OwnVoiceReader could not start its server: {exc}", file=sys.stderr)
        print("Install the server extras:  pip install 'ownvoicereader[server]'", file=sys.stderr)
        return 1

    try:
        start(config)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"OwnVoiceReader failed to bind the server: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
