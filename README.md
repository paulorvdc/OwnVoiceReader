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

This is at **Milestone M2 + M2.5** — figures/tables are explained at their first
reference, with an **optional** local vision LLM (M0–M1 closed; M2.5 done and
listenable). What works today:

- ✅ `ownvoicereader` starts a local server (127.0.0.1, fixed default port
  **6767**) and opens your browser to a clean viewer.
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
- ✅ **Optional vision** (M2.5) — with a local vision model (the `[vision]`
  extra + `ollama pull lfm3-vl:2b`) each figure and table gets a short spoken
  description of *what it shows* / *what its data means*; **without one the
  reader runs fully** and explains figures/tables from their **caption + context**
  instead (the A2 base) — it never stops, never goes silent. A status badge in the
  viewer shows the state. See *(Optional) Vision LLM* below.
- ✅ JSON API (`/api/info`, `/api/load`, `/api/unload`) + a PDF byte endpoint
  (`/pdf`, with HTTP range support for large files) + a `/ws` transport for
  events + audio.
- ✅ Server hygiene (A13): binds `127.0.0.1` only, a **fixed default port
  (6767)** that falls back to a free port if busy, local-only by
  default. Provider secrets are read from the OS environment **by name only**
  (never printed, never written to disk) — see *Security*.

What's **not here yet** (later milestones, see `BUILD_PLAN.md`):

- 🟢 **M2 + M2.5** figures & tables + **vision**: at each `Fig. 3` / `Table 2(b)`'s
  *first textual reference* the reader speaks a short explanation. With a local
  vision model (`lfm3-vl:2b` via the `ollama` library, the `[vision]` extra) it
  describes **what the figure shows** and **what the table's data means** — each
  figure and each table gets its *own* description (a same-kind, 2-D proximity link
  keeps a figure's pixels from being spoken at a table's reference). A **status
  badge** in the viewer (green = model reachable, red = "set up Ollama") shows the
  configured model; without one it falls back to a clean **caption+context** summary
  (A2 base). Vision is computed in the background at load, so it's ready by the
  reference and the read is never blocked. A text-only table grid summary (C3)
  remains an optional extra.
- 🔜 **M3** OCR (RapidOCR) so scanned papers read like text ones.
- 🔜 **M4** config layering + live switching of the LLM model.
- 🔜 **M5** selection: sections / text range / specific figures-tables.
- 🔜 **M6** hardening: graceful LLM-down, resume checkpoint, more tests.
- 🔜 **M7** UI revamp: a high-contrast, **low-vision-first** reader UI (a light
  default, a dark high-contrast option, and a Dracula theme), a lyrics-style
  now-reading / transcript, and the markdown of the table/figure being explained
  shown alongside.
- 🔜 **M8** packaging (post-v1): a cross-platform binary (`.exe` / AppImage /
  `.dmg`) that opens its own window while keeping the web app.

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
`pydantic`, `pydantic-settings`, `jinja2`, `websockets`,
`python-multipart`, `piper-tts`. The vision LLM (M2.5) is an optional
`[vision]` extra — the `ollama` library talking to a local Ollama server.

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

### 5. (Optional) Vision LLM — figure/table explanations (M2.5)

The vision LLM is **optional and non-essential** — it *enhances* each
figure/table explanation with a short spoken description of **what a figure shows**
and **what a table's data means**, computed in the background at load. The reader
**does not depend on it and never stops or goes silent without it**: when no vision
model is available it simply speaks the **caption + context** summary (the A2 base)
instead — one code path, no crash, no stall. This is the behavior that is coded:
the `NullLlmClient` makes every vision call return empty, the composer falls back
to the A2 base, and the vision work runs in a background *daemon* thread the
reading loop never waits on — so the read is **never silent and never blocked**.

When a vision model *is* present it talks to a **local Ollama** server via the
`ollama` library (the `[vision]` extra); nothing leaves the machine.

**Requirements to enable it** — every step is optional (skip all of them for a
text-only read):

```bash
pip install -e ".[vision]"        # 1. add the `ollama` library (core stays light)
ollama pull lfm3-vl:2b           # 2. the default vision model (small, ~1.5 GB)
# 3. Ollama's server must be running. Point the app at a non-default host with:
#      export OLLAMA_HOST=http://127.0.0.1:11434   # (the default) — or another server
# pick a different model with:  ownvoicereader paper.pdf --llm llama3.2-vision
#   (or export OWNVOICEREADER_LLM_MODEL=llama3.2-vision)
```

The model is chosen via `--llm` / `OWNVOICEREADER_LLM_MODEL` and the server via
`OLLAMA_HOST`; both are read from the OS environment and **never persisted** (A6).

**The viewer shows a status badge** (next to the Piper voice badge) so you always
know the vision state: a **green** chip naming the model in use
(`vision: lfm3-vl:2b`) when Ollama is reachable, or a **red** `vision: … · off`
chip when it is not — the red one is your cue that you still need to start Ollama
and/or pull the model. The badge is **read-only** (it reflects the *configured*
model, `client.model`; there is no in-UI picker yet — that is M4), and it
distinguishes "extra not installed" (`pip install ".[vision]"`) from
"installed but the server is down".

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

# Pin a specific port (default is 6767); 0 = a random free port
ownvoicereader --port 8000
ownvoicereader --port 0
```

A banner prints the local URL, e.g. `serving: http://127.0.0.1:6767/`. The
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
│  ├─ app.py               # FastAPI app + server runner (127.0.0.1, port 6767)
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
  on a **fixed default port (6767)** — easy to open and reason about — falling
  back to a free port if that one is already busy.
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
| `OWNVOICEREADER_PORT` | `6767` | bind port (`0` = a random free port) |
| `OWNVOICEREADER_PLAYBACK` | `browser` | `browser` (HTML5) / `system` (aplay) / `null` |
| `OWNVOICEREADER_LOCAL_ONLY` | `true` | forbid cloud egress |
| `OWNVOICEREADER_LLM_MODEL` | `lfm3-vl:2b` | vision/LLM model on local Ollama (M2.5); `--llm` overrides |
| `OLLAMA_HOST` | `http://localhost:11434` | the Ollama server (a different one can be pointed at) |
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

See `BUILD_PLAN.md` for the full milestone plan (M0–M8) and the locked design
decisions (A1–A14). **v1 = M0–M6**; **M7 (UI revamp, low-vision-first)** and
**M8 (cross-platform binary)** are post-v1. The vision LLM (M2.5) is optional
throughout — the reader runs fully without it (see *(Optional) Vision LLM*).
