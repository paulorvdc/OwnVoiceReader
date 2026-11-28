# Quick Start

This is the **guided install** for OwnVoiceReader: the **core** (a local,
cloud-free paper reader) plus **each optional extra**, one section per extra, with
the system dependencies each one needs. It is written to slot straight into the
project's ReadTheDocs site later — the heading levels, the relative cross-links,
and the callouts are already RTD-ready.

> **One-liner:** OwnVoiceReader renders an academic PDF and reads it **aloud** in a
> local voice, with a **local** LLM explaining each figure/table at its first
> reference. **Nothing leaves your machine**: the only LLM is the local **Ollama**
> server you run, and the voice is **Piper** on your machine. There are **no API
> keys and no cloud accounts** to set up.

---

## What you get by default

The **core** install gives you a fully working reader with **no extra setup**:

- A local web viewer (PDF.js) that opens your browser to a clean landing page.
- **Upload a PDF** (drag-drop or *Open a PDF…*) or point the CLI at one.
- The paper **read aloud** in a local Piper voice, reading the **2-column layout
  in the right order**, with a follow-along highlight, a "now reading" bar, and a
  live transcript.
- Media-style **voice controls**: play / pause / resume / stop / skip / replay,
  plus live **voice + style** switching (*academic-calm, quick-scan, narrative,
  lecture*).
- **Figures & tables** explained at their first textual reference, by **caption +
  context** (a clean spoken summary). This is the **base** — it needs no optional
  extra at all.
- **Config file** (`~/.ownvoicereader/config.yaml`) + **live switching** of
  model / voice / style / local-only / Ollama host, saved with one click.
- **Headless** read: `ownvoicereader paper.pdf --auto --no-browser`.

The three **optional extras** only *enhance* this core; the reader **never stops
or goes silent without any of them**.

---

## 1. Requirements

| Need | Core | Vision extra | OCR extra |
|---|---|---|---|
| **Python ≥ 3.10** | ✅ | ✅ | ✅ |
| A voice model (**~63 MB**, auto-downloaded on first run) | ✅ | | |
| A local **Ollama** server + a vision model | — | ✅ | |
| An OCR engine (**RapidOCR**, ships with its pip package) | — | | ✅ |
| `aplay`/ALSA *(Linux only, headless system audio)* | optional | optional | optional |
| A system **Tesseract** binary *(only if you pick the `tesseract` OCR engine)* | — | — | optional |

The recommended setup is a **conda environment**, so the heavy optional deps never
touch your base environment. The console command after install is
**`ownvoicereader`**.

> **Offline / locked-down note.** Core has **no network at runtime**: PDF.js is
> vendored in the package, and the only thing that ever downloads is the **Piper
> voice model** on first use. On a machine with no internet you can pre-fetch that
> model yourself (see §4, *Voice (Piper)*).

---

## 2. Install the core

### 2.1 Create the environment

```bash
conda create -n ownvoicereader python=3.13 -y
conda activate ownvoicereader
```

> **pip index gotcha.** If your pip is pointed at an unreachable *extra* index
> (e.g. NVIDIA's `pypi.ngc.nvidia.com`), point pip at the public index for this
> install. A ready-made config **ships in this repo** (use it for *any* extra below):
>
> ```bash
> PIP_CONFIG_FILE="$PWD/.pip-ownvoicereader.conf" pip install -e ".[test]"
> ```
>
> (Replace `.[test]` with the extra you want, or just `.` for core.)

### 2.2 Install the package

```bash
pip install -e .
```

Core is **voice-first**: `pymupdf`, `fastapi`, `uvicorn[standard]`, `pydantic`,
`pydantic-settings`, `jinja2`, `websockets`, `python-multipart`, **`piper-tts`**,
and `pyyaml` — the `pip install -e .` above already gives you the `piper` binary.
The heavy extras are gated so a bare install stays light:

| Extra | Adds | When you want it |
|---|---|---|
| `vision` | the `ollama` library → a local Ollama vision model | you want the LLM to *describe* figures/tables |
| `ocr` | `rapidocr` (+ `pytesseract` alt) | you have **scanned / image-only** PDFs |
| `server` | the web-server stack (already in core) | rarely needed on its own |
| `test` | `pytest` | you are running the test suite |
| `all` | `server` + `ocr` + `vision` | "just give me everything" |

You can install one or several at once, e.g. `pip install -e ".[vision,ocr]"`.

### 2.3 (Optional) PDF.js

The vendored PDF.js build ships in `ownvoicereader/viewer/vendor/pdfjs/`, so the
app works **offline / local-only with no network at runtime**. Nothing to do.

### 2.4 (Optional) Desktop app — the native installers (M6)

A **single, self-contained, offline** package per OS ships on the **GitHub
Releases** page (no Python, no setup — it opens the viewer in its own window):
`OwnVoiceReader-x86_64.AppImage` (plus `ownvoicereader_0.1.0_amd64.deb`, the
one-click **Ubuntu/Debian** installer) on **Linux**, `OwnVoiceReader-Setup-x86_64.exe`
on **Windows**, and `OwnVoiceReader-arm64.dmg` on **macOS (Apple Silicon only)**.

> **Got an Intel Mac?** The macOS `.dmg` is arm64 and won't run on Intel. Use the
> **command-line version** instead — it works on any architecture:
> `pip install "ownvoicereader[server,ocr,vision]"`, then
> `ownvoicereader paper.pdf` (opens the browser) or
> `ownvoicereader paper.pdf --no-browser` (headless). You lose only the native
> window — the voice, OCR, and vision are identical. See the README's *Desktop app*.

---

## 3. Run it

```bash
# Landing page + drag-drop upload (the primary flow)
ownvoicereader

# Open a specific PDF directly (convenience / headless)
ownvoicereader path/to/paper.pdf

# No browser (headless / CI); reads the paper to completion
ownvoicereader path/to/paper.pdf --auto --no-browser

# Headless with the system audio device instead of the browser
ownvoicereader path/to/paper.pdf --auto --no-browser --playback system
```

A banner prints the local URL, e.g. `serving: http://127.0.0.1:6767/`. The browser
opens automatically; press **Ctrl-C** to stop. The server binds `127.0.0.1` (never
your LAN) on a **fixed default port `6767`**, falling back to a free port if that
one is busy.

| Flag | Effect |
|---|---|
| `--host` | bind host (default `127.0.0.1`) |
| `--port N` | bind port (default `6767`; `0` = a random free port) |
| `--no-browser` | do not open a browser (headless / CI) |
| `--auto` | headless start-to-finish read |
| `--playback browser\|system\|null` | where audio plays (default `browser`) |
| `--llm <model>` | vision/LLM model on local Ollama (default `lfm3-vl:2b`) |
| `--voice <id>` | TTS voice id |
| `--style <preset>` | one of *academic-calm / quick-scan / narrative / lecture* |
| `--local-only` | forbid any cloud egress (**the default**) |
| `-v` / `-vv` | more logging |

### The UI-upload flow

1. Run `ownvoicereader` (no arguments).
2. The browser opens to a drop zone.
3. **Drag a PDF onto the page** (or click *Open a PDF…*).
4. The PDF uploads to the local server and renders instantly.
5. Use page navigation / zoom in the control bar.

> The PDF is stored in a temporary directory for the session and deleted when the
> server stops. No copy is kept on disk beyond the run.

---

## 4. Voice (Piper) — ships in core

TTS is **Piper**, a local neural TTS engine (**no cloud, no API key**). Because a
paper reader *is* a voice tool, `piper-tts` ships in the **core** install — the
`pip install -e .` above already gave you the `piper` binary. Only the **voice
model** (~63 MB, `en_US-lessac-medium`) is fetched on first use, into
`~/.cache/ownvoicereader/voices/`.

```bash
# the `piper` binary already comes with the core install — nothing to install here
# first run downloads en_US-lessac-medium automatically …
# …or fetch a voice yourself (a handy fallback for offline / locked-down setups):
curl -L -o ~/.cache/ownvoicereader/voices/en_US-lessac-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
```

Point the app at a different Piper binary or voice dir with
`OWNVOICEREADER_PIPER_BIN` / `OWNVOICEREADER_VOICE_DIR`. If **no** Piper binary or
voice is present, the app still runs — it reports the voice as *unavailable* in the
UI and skips TTS (headless `--auto` reads with a `null` player and produces no
sound).

> **Headless system audio.** The `--playback system` path uses **`aplay`** (ALSA)
> on Linux (`ffplay` if present); a missing audio device is handled gracefully.

---

## 5. Optional extras

Each extra is **independent** and **graceful**: without it the reader still runs,
it just uses the base path (caption + context) instead of the enhanced one.

### 5.1 `[vision]` — figure/table explanations (the LLM describes them)

The vision LLM **enhances** each figure/table explanation with a short spoken
description of **what a figure shows** and **what a table's data means**, computed
in the background at load. It is **optional and non-essential**: with no vision
model the reader simply speaks the **caption + context** summary instead — one code
path, no crash, no stall. When a model *is* present it talks to a **local Ollama**
server via the `ollama` library; nothing leaves the machine.

```bash
pip install -e ".[vision]"        # 1. add the `ollama` library (core stays light)
ollama pull lfm3-vl:2b           # 2. the default vision model (small, ~1.5 GB)
# 3. Ollama's server must be running. Point the app at a non-default host with:
#      export OLLAMA_HOST=http://127.0.0.1:11434   # (the default) — or another server
# pick a different model:
#      ownvoicereader paper.pdf --llm llama3.2-vision
#      export OWNVOICEREADER_LLM_MODEL=llama3.2-vision
```

- **System requirement:** a running **Ollama** server (install from
  ollama.com; run `ollama serve`). The default model is **`lfm3-vl:2b`**.
- **Where to point it:** `--llm` / `OWNVOICEREADER_LLM_MODEL` for the *model*,
  `OLLAMA_HOST` for the *server*. In the viewer you can also set a **different
  Ollama host** with the **⟳ Refresh models** button and **save** it (M4).
- **The status badge** (next to the Piper voice badge) tells you the vision state:
  a **green** chip naming the model in use (`vision: lfm3-vl:2b`) when Ollama is
  reachable, or a **red** `vision: … · off` chip when it is not — your cue that you
  still need to start Ollama and/or pull the model. The badge distinguishes
  "*extra not installed*" (`pip install .[vision]`) from "*installed but the
  server is down*".

### 5.2 `[ocr]` — read scanned / image-only PDFs

A page with **no text layer** (a scanned or "print-as-image" PDF) is read with a
local OCR engine so it reads the **same** as a typed one. The default is
**RapidOCR** (`rapidocr`, fully local — its models ship with the package, **no
download**); **Tesseract** is the alt. Without the extra, a scanned page is simply
left unread and the typed pages still read — one code path, no crash. A typed-only
PDF pays nothing (the engine is built only when a page needs it).

```bash
pip install -e ".[ocr]"          # 1. add RapidOCR (+ the pytesseract alt)
# a scanned PDF is read automatically — no flag, no config:
ownvoicereader path/to/scanned.pdf            # open it in the viewer
ownvoicereader path/to/scanned.pdf --auto     # headless read of a scan
# pick the alt engine / resolution from the OS environment (A6: never persisted):
#   export OWNVOICEREADER_OCR_ENGINE=tesseract
#   export OWNVOICEREADER_OCR_DPI=300
```

- **`rapidocr` (default):** pure-Python, its models ship in the pip package —
  **no system binary, no download.**
- **`tesseract` (alt):** drives the **system `tesseract` binary**, which you must
  install yourself (`apt install tesseract-ocr`, `brew install tesseract`, etc.).
  Enable it with `OWNVOICEREADER_OCR_ENGINE=tesseract`.
- Resolution for rasterized pages: `OWNVOICEREADER_OCR_DPI` (default `300`).

### 5.3 `[server]`, `[test]`, `[all]`

- **`[server]`** lists the web-server stack (`fastapi`, `uvicorn[standard]`,
  `jinja2`, `websockets`, `python-multipart`) — these are **already in core**, so
  you rarely need it on its own. It is what the CLI names if it can't import the
  server: `pip install 'ownvoicereader[server]'`.
- **`[test]`** adds `pytest`; install it to run the suite (see *Testing*).
- **`[all]`** is `server` + `ocr` + `vision` — "just give me everything".

---

## 6. Configuration

Settings layer, **highest precedence last**:

```
defaults  →  ~/.ownvoicereader/config.yaml  →  OWNVOICEREADER_* env  →  CLI flags
```

- The **config file** holds your **durable** choices — `host`, `port`,
  `open_browser`, `local_only`, `playback`, `director`, `llm_provider`,
  `llm_model`, `ollama_host`, `voice`, `style` — written by the viewer's
  **💾 Save** button and read on launch. The per-run `pdf_path` argument is
  **never** saved.
- A host *endpoint* (`ollama_host`, the local Ollama server) is **not** a secret,
  so it **is** saved; **no credential is ever written to disk** (A6).
- The file is **optional and best-effort**: a missing or malformed `config.yaml` is
  ignored (the reader degrades to the lower layer, it never crashes). Point it
  elsewhere with `OWNVOICEREADER_CONFIG=path/to/config.yaml`.

```yaml
# OwnVoiceReader settings — written by the viewer's 'Save' button.
llm_model: lfm3-vl:2b
ollama_host: http://10.0.0.5:11434
voice: en_US-lessac
style: academic-calm
local_only: true
```

Environment variables (prefix `OWNVOICEREADER_`):

| Variable | Default | Meaning |
|---|---|---|
| `OWNVOICEREADER_HOST` | `127.0.0.1` | bind host |
| `OWNVOICEREADER_PORT` | `6767` | bind port (`0` = a random free port) |
| `OWNVOICEREADER_PLAYBACK` | `browser` | `browser` (HTML5) / `system` (aplay) / `null` |
| `OWNVOICEREADER_LOCAL_ONLY` | `true` | forbid cloud egress |
| `OWNVOICEREADER_LLM_MODEL` | `lfm3-vl:2b` | vision/LLM model on local Ollama |
| `OWNVOICEREADER_OLLAMA_HOST` | `http://localhost:11434` | the Ollama server the vision LLM uses (the **saved** host, overridable by env, settable live) |
| `OLLAMA_HOST` | `http://localhost:11434` | the `ollama` library's own var, used as the fallback when no host is saved |
| `OWNVOICEREADER_VOICE` | `en_US-lessac-medium` | Piper voice id |
| `OWNVOICEREADER_STYLE` | `academic-calm` | TTS style preset |
| `OWNVOICEREADER_VOICE_DIR` | `~/.cache/ownvoicereader/voices` | Piper voice dir |
| `OWNVOICEREADER_PIPER_BIN` | auto (`piper`) | override the Piper binary path |
| `OWNVOICEREADER_ESPEAK_BIN` | auto | override the espeak-ng binary path |
| `OWNVOICEREADER_OCR_ENGINE` | `rapidocr` | OCR engine: `rapidocr` (default) / `tesseract` |
| `OWNVOICEREADER_OCR_DPI` | `300` | rasterization resolution for OCR'd pages |
| `OWNVOICEREADER_CONFIG` | `~/.ownvoicereader/config.yaml` | the saved-settings file |

---

## 7. Testing

```bash
pip install -e ".[test]"
pytest            # hermetic: generates its own PDF fixtures; skips heavy extras cleanly
```

The suite is hermetic — it generates its own PDF fixtures and **skips** tests that
need an optional extra (OCR, a live Ollama vision model, a real Piper binary) when
that extra or sample is absent, so a bare `pytest` always runs green.

---

## 8. Common problems

| Symptom | Fix |
|---|---|
| `pip install` stalls or fails | Your pip points at an unreachable extra index. Use `PIP_CONFIG_FILE="$PWD/.pip-ownvoicereader.conf"` (see §2.1). |
| `ownvoicereader: command not found` | The env isn't activated, or the install didn't finish. `conda activate ownvoicereader`, then `pip install -e .` again. |
| `could not start its server` / "Install the server extras" | `pip install -e ".[server]"`. |
| Voice *unavailable* in the UI | No Piper voice is present. Let the first run download it, or pre-fetch it (see §4). |
| `vision: … · off` (red badge) | Start Ollama (`ollama serve`) and/or `ollama pull lfm3-vl:2b`; the base caption+context read still works. |
| Scanned page reads nothing | Install `[ocr]` (`pip install -e ".[ocr]"`); a scanned page is left unread without it. |
| Headless `--playback system` is silent | No `aplay`/ALSA device on this machine; audio degrades gracefully (nothing crashes). |
| Port `6767` is busy | The server auto-falls-back to a free port; pin another with `--port N` (or `--port 0` for a random one). |
| `espeak-ng` wanted instead of Piper | Set `OWNVOICEREADER_ESPEAK_BIN` to a working espeak-ng binary; the engine auto-selects it if a binary ever appears. |

---

## 9. What's local, what's not

- **Local-only by default.** The server binds `127.0.0.1` (never your LAN) and
  `--local-only` is the default (and the only mode) — there is **no** cloud option
  to opt into.
- **No cloud egress, ever.** The app pings **no proprietary cloud provider**; the
  whole configuration surface (the env allow-list + the settings the "Save" button
  writes) must stay clear of every proprietary provider — guarded by
  `tests/test_config.py::test_no_credentials_are_ever_persisted`.

---

## Next

This page is the install guide; the full architecture, milestone plan, and locked
design decisions (A1–A13) live in [`../BUILD_PLAN.md`](../BUILD_PLAN.md), and the
project overview in [`../README.md`](../README.md). When this project grows a
ReadTheDocs site, this file is intended to be the **`quickstart`** page.
