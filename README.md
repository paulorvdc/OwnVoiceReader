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

This is **Milestone M0 — Skeleton: viewer + server**. It is the first
testable milestone. What works today:

- ✅ `ownvoicereader` starts a local server (127.0.0.1, random high port) and
  opens your browser to a clean viewer.
- ✅ **Upload a PDF in the UI** — drag-drop onto the landing page or use the
  "Open a PDF…" button. The PDF is served back to the browser over HTTP.
- ✅ **PDF.js** renders the document (page navigation, zoom, fit-width, keyboard).
- ✅ `ownvoicereader paper.pdf` pre-loads a PDF (for headless/CI / convenience).
- ✅ JSON API (`/api/info`, `/api/load`, `/api/unload`) + a PDF byte endpoint
  (`/pdf`, with HTTP range support for large files).
- ✅ Server hygiene (A13): binds `127.0.0.1` only, random port, local-only by
  default. Provider secrets are read from the OS environment **by name only**
  (never printed, never written to disk) — see *Security*.

What's **not here yet** (later milestones, see `BUILD_PLAN.md`):

- 🔜 **M1** text reader: extract blocks, reading order, TTS (Piper), streamed
  audio to the browser, play / pause / stop / skip / replay.
- 🔜 **M2** figures & tables: pdfplumber tables, figure crops, LLM explanations
  at the *first textual reference* (`Fig. 3` / `Table 2(b)`).
- 🔜 **M3** OCR (RapidOCR) so scanned papers read like text ones.
- 🔜 **M4** config layering + live switching of model / voice / style.
- 🔜 **M5** selection: sections / text range / specific figures-tables.
- 🔜 **M6** hardening: graceful LLM-down, resume checkpoint, tests.

The reader control bar (play/pause/…) is visible but **disabled** until M1.

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

---

## Run

```bash
# Landing page + drag-drop upload (the primary flow)
ownvoicereader

# Open a specific PDF directly (convenience / headless)
ownvoicereader path/to/paper.pdf

# No browser (headless / CI); reads to completion once M1 lands
ownvoicereader path/to/paper.pdf --auto --no-browser

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

## How it works (M0)

```
ownvoicereader/            # the Python package
├─ ingest/pdf_source.py     # open PDF → page count + metadata (M0)
├─ model.py                # Block / ReadingDocument / Reference / ReadingEvent
├─ config.py               # defaults → env (OWNVOICEREADER_*) → CLI flags
├─ server/
│  ├─ app.py               # FastAPI app + server runner (127.0.0.1, random port)
│  ├─ api.py               # /api/info, /api/load, /api/unload
│  └─ stream.py            # /ws transport (M1: events + audio)
├─ viewer/
│  ├─ index.html           # landing + PDF.js render + control bar
│  ├─ transport.js         # API client, PDF.js render, upload, nav
│  └─ vendor/pdfjs/        # vendored PDF.js (offline)
└─ cli.py                  # the `ownvoicereader` entry point
```

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

## Configuration (M0)

Config layers are **defaults → environment → CLI flags** (the YAML layer
`~/.ownvoicereader/config.yaml` arrives in M4).

Environment variables (prefix `OWNVOICEREADER_`):

| Variable | Default | Meaning |
|---|---|---|
| `OWNVOICEREADER_HOST` | `127.0.0.1` | bind host |
| `OWNVOICEREADER_PORT` | *(random)* | bind port |
| `OWNVOICEREADER_PLAYBACK` | `browser` | `browser` (HTML5) or `system` |
| `OWNVOICEREADER_LOCAL_ONLY` | `true` | forbid cloud egress |
| `OWNVOICEREADER_LLM_MODEL` | — | e.g. `ollama/qwen3:8b` |
| `OWNVOICEREADER_VOICE` | — | TTS voice id |
| `OWNVOICEREADER_STYLE` | — | `academic-calm` / `quick-scan` / `narrative` / `lecture` |

---

## Testing

```bash
pip install -e ".[test]"
pytest            # hermetic: generates its own PDF fixture
```

---

## Roadmap

See `BUILD_PLAN.md` for the full milestone plan (M0–M7) and the locked design
decisions (A1–A14). **v1 = M0–M6**; M7 (cross-platform binary) is deferred.
