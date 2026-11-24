# OwnVoiceReader

> **your model, your voice, your machine.**

OwnVoiceReader is a lightweight **local web app** that renders an academic PDF,
lets you read the **whole paper or a selection** (sections / a text range /
specific figures & tables) aloud in a chosen **voice + style**, and uses
**your LLM** (local Ollama or a cloud provider you opt into) to explain
figures and tables **at the right moment**. Text and scanned (OCR) PDFs are
handled interchangeably.

You **upload the PDF in the UI** (drag-drop or file picker). The CLI can also
open a PDF directly — that path exists for headless / CI use
(`--auto --no-browser`).

---

## Status

This is at **Milestone M1 — Text reader** (built 2026-11-24, awaiting your
audio smoke test). What works today:

- ✅ `ownvoicereader` starts a local server (127.0.0.1, random high port) and
  opens your browser to a clean viewer.
- ✅ **Upload a PDF in the UI** — drag-drop onto the landing page or use the
  "Open a PDF…" button. The PDF is served back to the browser over HTTP.
- ✅ **PDF.js** renders the document (page navigation, zoom, fit-width, keyboard).
- ✅ **Reads the paper aloud** in a local voice (Piper), reading the
  **2-column layout in the right order** (left column fully, then the right),
  with a follow-along highlight, a "now reading" bar, and a live transcript.
- ✅ **Voice controls** — play / pause / resume / **stop / skip / replay**
  (media-style), and live **voice + style** switching (4 styles:
  *academic-calm, quick-scan, narrative, lecture*).
- ✅ **Headless read** — `ownvoicereader paper.pdf --auto --no-browser` speaks
  the whole paper to a system or null player (no browser needed).
- ✅ JSON API (`/api/info`, `/api/load`, `/api/unload`) + a PDF byte endpoint
  (`/pdf`, with HTTP range support for large files) + a `/ws` transport for
  events + audio.
- ✅ Server hygiene (A13): binds `127.0.0.1` only, random port, local-only by
  default. Provider secrets are read from the OS environment **by name only**
  (never printed, never written to disk) — see *Security*.

What's **not here yet** (later milestones, see `BUILD_PLAN.md`):

- 🔜 **M2** figures & tables: pdfplumber tables, figure crops, LLM explanations
  at the *first textual reference* (`Fig. 3` / `Table 2(b)`).
- 🔜 **M3** OCR (RapidOCR) so scanned papers read like text ones.
- 🔜 **M4** config layering + live switching of the LLM model.
- 🔜 **M5** selection: sections / text range / specific figures-tables.
- 🔜 **M6** hardening: graceful LLM-down, resume checkpoint, more tests.

> **Voice engine:** M1 uses **Piper** as the local voice
> (`en_US-lessac-medium`, 22050 Hz). espeak-ng was the plan's first choice but
> could not be provisioned on this machine (see `BUILD_PLAN.md` M1 note); the
> `TtsEngine` abstraction still auto-selects espeak-ng if a binary ever appears.
> Piper is the **only** cloud-free TTS in core — no cloud TTS is used.

---

## Install

OwnVoiceReader runs in an isolated environment. A **conda env** is recommended
(so the heavy optional deps never touch your base environment). The console
command after install is `ownvoicereader`.

### 1. Create the environment

```bash
conda create -n ownvoicereader python=3.13 -y
conda activate ownvoicereader
```

> **pip index note:** if your pip is configured with an unreachable extra index
> (e.g. NVIDIA's `pypi.ngc.nvidia.com`), point pip at the public index for the
> install. A ready-made config ships in this repo:
>
> ```bash
> PIP_CONFIG_FILE="$PWD/.pip-ownvoicereader.conf" pip install -e ".[test]"
> ```

### 2. Install the package (core = M0)

```bash
pip install -e .
```

Core deps are intentionally light: `pymupdf`, `fastapi`, `uvicorn`,
`pydantic`, `pydantic-settings`, `jinja2`, `httpx`, `websockets`,
`python-multipart`.

### 3. (Optional) PDF.js

The vendored PDF.js build already ships in
`ownvoicereader/viewer/vendor/pdfjs/` (downloaded at setup), so the app works
**offline / local-only** with no network at runtime.

### 4. Voice (Piper) — needed for M1 TTS

TTS is powered by **Piper**, a local neural TTS engine (no cloud, no API key).
The `piper-tts` wheel provides the `piper` binary; a voice model (~63 MB) is
downloaded on first use into `~/.cache/ownvoicereader/voices/` (override with
`OWNVOICEREADER_VOICE_DIR`):

```bash
pip install "piper-tts"          # provides the piper binary
# first run downloads en_US-lessac-medium automatically
# …or fetch it yourself:
#   curl -L -o ~/.cache/ownvoicereader/voices/en_US-lessac-medium.onnx \
#     https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
```

Point the app at a different Piper binary or voice dir with
`OWNVOICEREADER_PIPER_BIN` / `OWNVOICEREADER_VOICE_DIR`. If no Piper binary or
voice is present, the app still runs — it just reports the voice as
*unavailable* in the UI and skips TTS (headless `--auto` will read with a
`null` player and produce no sound).

> Audio output for the **headless** path uses **`aplay`** (ALSA) when
> `--playback system`; a missing audio device is handled gracefully.

---

## Run

```bash
# Landing page + drag-drop upload (the primary flow)
ownvoicereader

# Open a specific PDF directly (convenience / headless)
ownvoicereader path/to/paper.pdf

# No browser (headless / CI); reads the paper to completion
ownvoicereader path/to/paper.pdf --auto --no-browser

# Headless with the system audio device instead of the browser
ownvoicereader path/to/paper.pdf --auto --no-browser --playback system

# Pin the port instead of a random high one
ownvoicereader --port 8000
```

A banner prints the local URL, e.g. `serving: http://127.0.0.1:51234/`. The
browser opens automatically; press **Ctrl-C** to stop.

### The UI-upload flow (M0)

1. Run `ownvoicereader` (no arguments).
2. The browser opens to a drop zone.
3. **Drag a PDF onto the page** (or click *Open a PDF…*).
4. The PDF uploads to the local server and renders instantly.
5. Use page navigation / zoom in the control bar.

> The PDF is stored in a temporary directory for the session and deleted when
> the server stops. No copy is kept on disk beyond the run.

---

## How it works (M1)

```
ownvoicereader/            # the Python package
├─ ingest/
│  ├─ pdf_source.py        # open PDF → page count + metadata (M0)
│  ├─ text_extractor.py    # lines + bboxes; flag sparse/empty pages (M1)
│  └─ reading_order.py     # 2-col L→R, then down; merge → blocks (M1)
├─ model.py                # Block / ReadingDocument / Reference / ReadingEvent
├─ config.py               # defaults → env (OWNVOICEREADER_*) → CLI flags
├─ voice/
│  ├─ styles.py            # 4 presets: academic-calm / quick-scan / …
│  ├─ tts_engine.py        # Piper (local) + espeak-ng fallback, WAV wrap
│  └─ player.py            # system (aplay) / null / browser players
├─ reader/
│  └─ orchestrator.py      # cues; pause/resume/stop/skip/replay (M1)
├─ server/
│  ├─ app.py               # FastAPI app + server runner (127.0.0.1, random port)
│  ├─ api.py               # /api/info, /api/load, /api/unload (+ voice info)
│  └─ stream.py            # /ws transport: events + audio + controls (M1)
├─ viewer/
│  ├─ index.html           # landing + PDF.js + control bar + transcript
│  ├─ transport.js         # WS client + audio queue + highlight + controls
│  └─ vendor/pdfjs/        # vendored PDF.js (offline)
└─ cli.py                  # the `ownvoicereader` entry point
```

At load time the ingest layer turns the PDF into an ordered list of `Block`s
(2-column reading order, page numbers / running headers filtered out). The
reader turns those into short **cues** (~200 chars), and Piper synthesises each
cue into a 16-bit mono **WAV** streamed to the browser over one binary WS frame.
The browser pulls the next cue with a 1-cue lookahead, so **pause/stop are
instant** and the highlight + transcript follow along.

The **unified `Block`** shape (see `BUILD_PLAN.md` §4) is the contract that
lets text and OCR feed the *same* reader pipeline later:

```
Block(kind: Text|Figure|Table, source: text|ocr, text, image,
      page, bbox, caption, table_grid)
```

---

## Security

- **Local-only by default (A13).** The server binds `127.0.0.1` (never your LAN)
  and picks a **random high port** so it doesn't collide or get guessed.
- **No cloud egress by default.** `--local-only` is the default; provider
  access is opt-in.
- **Secrets stay in your environment (A6).** API keys (`OPENAI_API_KEY`,
  `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, …) are read from the
  OS environment **only**. The UI shows *which* keys are present **by name**
  (e.g. a small `OPENAI_API_KEY` badge) but **never the value**, and nothing is
  written to disk. This keeps credential handling entirely in your hands.

---

## Configuration (M1)

Config layers are **defaults → environment → CLI flags** (the YAML layer
`~/.ownvoicereader/config.yaml` arrives in M4).

Environment variables (prefix `OWNVOICEREADER_`):

| Variable | Default | Meaning |
|---|---|---|
| `OWNVOICEREADER_HOST` | `127.0.0.1` | bind host |
| `OWNVOICEREADER_PORT` | *(random)* | bind port |
| `OWNVOICEREADER_PLAYBACK` | `browser` | `browser` (HTML5) / `system` (aplay) / `null` |
| `OWNVOICEREADER_LOCAL_ONLY` | `true` | forbid cloud egress |
| `OWNVOICEREADER_LLM_MODEL` | — | e.g. `ollama/qwen3:8b` (used from M2) |
| `OWNVOICEREADER_VOICE` | `en_US-lessac-medium` | Piper voice id |
| `OWNVOICEREADER_STYLE` | `academic-calm` | TTS style preset |
| `OWNVOICEREADER_VOICE_DIR` | `~/.cache/ownvoicereader/voices` | Piper voice dir |
| `OWNVOICEREADER_PIPER_BIN` | auto (env `piper`) | override Piper binary path |
| `OWNVOICEREADER_ESPEAK_BIN` | auto | override espeak-ng binary path |

---

## Testing

```bash
pip install -e ".[test]"
pytest            # hermetic: generates its own PDF fixtures
```

M1 adds `tests/test_reading_order.py` (2-column ordering, gutter detection,
page-number / stray-line filtering, a generated 2-column PyMuPDF PDF) and
`tests/test_voice.py` (WAV wrapping + style presets; a Piper smoke test that
skips cleanly when no engine is present).

---

## Roadmap

See `BUILD_PLAN.md` for the full milestone plan (M0–M7) and the locked design
decisions (A1–A14). **v1 = M0–M6**; M7 (cross-platform binary) is deferred.
