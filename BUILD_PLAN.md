# OwnVoiceReader — Build Plan

> **Tagline:** *your model, your voice, your machine.*
> A lightweight **local web app** that renders an academic PDF, lets you read the
> **whole paper or a selection** (sections / text range / specific figures-tables),
> and reads it aloud in a chosen **voice + style** — using **your LLM** (cloud or
> local Ollama) to explain figures and tables at the **right moment**. Handles
> text **and** scanned (OCR) PDFs interchangeably.

This document is the **build-mode** plan. It uses **checkboxes** for task status
and **`📝 comment:`** lines for your input. **Do not start coding until Section 1
(decisions) is reviewed and checked off.**

---

## 0. How to use this file

- **Checkboxes:** `- [ ]` not started · `- [~]` in progress · `- [x]` done.
- **Comment lines:** every `> 📝 comment:` is a blank space for your notes,
  objections, or a decision. Grep `📝 comment` to find them all.
- **Decision lines** (Section 1) end in `- [ ] accept / - [ ] override`. Tick one.
- **Legend for milestones:** `M0` skeleton → `M7` packaging. v1 = M0–M6.
- **Single source of truth:** this file supersedes the earlier architecture
  sketch in `~/.opencode/plan/plan.md`.

> 📝 comment: _(overall notes / anything that changes the whole direction)_

---

## 1. Decisions — confirm before build

> Tick **accept** or **override** each. Override → write your value in the comment.

### 1.1 Locked earlier (re-confirm)
- [ x] **A1 TTS default** — offline-first **Piper**, cloud (OpenAI/ElevenLabs) opt-in
  - [ ] accept  - [ x] override → no cloud option for TTS
  > 📝 comment:
- [ x] **A2 No-vision fallback** — caption + context **text summary** (mandatory, not optional)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [x ] **A3 Table detector** — **pdfplumber** + vision fallback for unstructured tables
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [x ] **A4 OCR engine** — **RapidOCR** default, Tesseract alt
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [x ] **A5 Interface** — **local web app** (Python server + browser **PDF.js** viewer)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A6 LLM client** — **LiteLLM** + thin Ollama path (one interface for all providers)
  - [ ] accept  - [ ] override →
  > 📝 comment: make sure I can choose the model somewhere on the interface to match my machine and keep minimal what needs to be installed prior and allow some API access here if the user wants. use tokens from the OS env and make that clear for the user (keeps these security concerns out of our hands)
- [ x] **A7 Explanation timing** — **first textual reference** default; `--director` opt-in (LLM places them)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A8 Reader styles** — 4 presets (`academic-calm`, `quick-scan`, `narrative`, `lecture`) + user override
  - [ x] accept  - [ ] override →
  > 📝 comment:

### 1.2 New — need your call
- [ x] **A9 Audio playback location** — **in-browser HTML5 `<audio>`** default; `--playback system` for headless/OS audio
  - [ x] accept  - [ ] override →
  > 📝 comment: allow for keyboard/headphone media controls rollback and forward to work besides play and pause
- [ x] **A10 Section detection** — v1 = **font-size + numbering heuristics** (PyMuPDF font metadata + `§3.1` regex); LLM refine optional flag
  - [ x] accept  - [ ] override →
  > 📝 comment: fallback to llm
- [x ] **A11 Packaging** — v1 = `pip install ownvoicereader` console script (`ownvoicereader`); true cross-platform **binary** (PyInstaller/Briefcase) deferred to M7
  - [ x] accept  - [ ] override →
  > 📝 comment: yes pip install but create setup.py and let me handle distribution later
- [ x] **A12 Headless/CI** — keep `ownvoicereader paper.pdf --auto --no-browser` (full auto-read to system audio, no UI)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A13 Server security** — bind **`127.0.0.1`** only, **random high port**, `--local-only` forbids any cloud egress
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A14 Resume** — JSON **checkpoint** of reading position so a paper can be reopened mid-read
  - [ x] accept  - [ ] override →
  > 📝 comment:

### 1.3 Open (your call — answer or skip)
- [ x] **Dev OS / environment** — Linux now; cross-platform later? _(Linux assumed)_
  > 📝 comment: cross-platform on anything that can run python i.e.: windows and mac too
- [ x] **Local LLM now?** — will you have **Ollama** running for dev, or cloud-only first?
  > 📝 comment: local only first
- [ x] **First sample paper** — which PDF do we validate reading-order + selection on?
  > 📝 comment: test_pdfs/3799682.3840189.pdf
- [x ] **Language** — English-only for v1, or multilingual from the start?
  > 📝 comment: english-only v1

---

## 2. Milestones (check as you go)

### M0 — Skeleton: viewer + server  _status: [x] done_
- [x] `pyproject.toml` with `ownvoicereader` console entry point + extras (`[local] [cloud] [ocr] [vision] [server]`)
- [x] `ingest/pdf_source.py` — open PDF, page count, metadata
- [x] `server/app.py` — FastAPI/uvicorn on `127.0.0.1:<port>`, auto-opens browser
- [x] `viewer/index.html` + **PDF.js** renders the PDF
- [x] `ownvoicereader paper.pdf` opens the viewer; `ownvoicereader` (no args) → landing page + drag-drop
> 📝 comment: DONE 2026-11-24. Package renamed `ownvoice`→`ownvoicereader` (exclusivity); conda env `ownvoicereader`; isolated pip config `.pip-ownvoicereader.conf` (bypasses broken NVIDIA index). **Primary flow = upload PDF in the UI** (drag-drop / picker → `POST /api/load` → renders). CLI `paper.pdf` arg kept only for convenience + headless `--auto`. `/pdf` is a top-level route with HTTP range support (206 + `accept-ranges`). Provider secrets shown in UI by name only (never value). Tests: `tests/test_pdf_source.py` (hermetic). PDF.js vendored offline.


### M1 — Text reader  _status: [ ] not started_
- [ ] `ingest/text_extractor.py` — text blocks + bboxes; flag sparse/empty pages
- [ ] `ingest/reading_order.py` — merge blocks into one ordered sequence
- [ ] `voice/tts_engine.py` — **espeak-ng first** (smoke test), then **Piper**
- [ ] `voice/player.py` — stream audio to **browser** (HTML5 `<audio>`)
- [ ] `server/stream.py` — WebSocket/SSE: position, transcript, events
- [ ] `viewer/transport.js` — WS/SSE client + audio + control buttons
- [ ] `reader/orchestrator.py` — read text aloud; **pause / resume / stop / skip / replay**
> 📝 comment:

### M2 — Figures & tables  _status: [ ] not started_
- [ ] `ingest/figure_table.py` — pdfplumber (tables) + image crops (figures)
- [ ] `llm/client.py` — **LiteLLM** + thin Ollama path; streaming; vision inputs
- [ ] `llm/explainer.py` — figure→**vision** prompt; table→**grid summary**; *spoken*, short, no LaTeX/markdown
- [ ] `reader/references.py` — detect `Fig. 3 / Figure 3 / Fig. 3a / Table 2(b)`; build reference index
- [ ] `reader/timeline.py` — **"right-moment"** engine: first-reference `EXPLAIN` events
- [ ] orchestrator emits + speaks `EXPLAIN` events
> 📝 comment:

### M3 — OCR (text/OCR interchangeable)  _status: [ ] not started_
- [ ] `ingest/ocr.py` — **RapidOCR** fallback for pages/regions without a text layer
- [ ] OCR emits the **same `Block` shape** as `text_extractor`
- [ ] **parity test**: a scanned paper reads identically to a text-based one
> 📝 comment:

### M4 — Config & models  _status: [ ] not started_
- [ ] `config.py` — layering: defaults → `~/.ownvoicereader/config.yaml` → env `OWNVOICEREADER_*` → CLI flags
- [ ] live change of **model / voice / style** from the viewer UI
- [ ] `--local-only` (no cloud egress) + Ollama-first path
> 📝 comment:

### M5 — Selection  _status: [ ] not started_
- [ ] `reader/sections.py` — heading detection (font-size + numbering; optional LLM refine)
- [ ] `reader/scope.py` — selection → filtered, reading-ordered timeline slice
- [ ] viewer selection UI: **whole / sections / free-form text range / specific figures-tables / combine**
- [ ] explanations fire for figures/tables **referenced within the read scope** + explicitly selected
> 📝 comment:

### M6 — Hardening  _status: [ ] not started_
- [ ] graceful **LLM-down** degradation: keep reading text, skip explanations, show notice
- [ ] **resume checkpoint** (JSON position, A14)
- [ ] error handling + unit tests (ingest, references, timeline, scope)
> 📝 comment:

### M7 — Packaging (later, post-v1)  _status: [ ] not started_
- [ ] **PyInstaller / Briefcase** cross-platform binary (`.exe` / AppImage / `.dmg`)
- [ ] `--auto --no-browser` headless polish (A12)
> 📝 comment:

---

## 3. File / module layout (create as you go)

```
ownvoicereader/
├─ ingest/
│  ├─ pdf_source.py      [ ]
│  ├─ text_extractor.py  [ ]
│  ├─ ocr.py             [ ]
│  ├─ figure_table.py    [ ]
│  └─ reading_order.py   [ ]
├─ model.py              [ ]   # ReadingDocument, Block, Reference, ReadingEvent
├─ reader/
│  ├─ references.py      [ ]
│  ├─ timeline.py        [ ]   # "right-moment" engine
│  ├─ orchestrator.py    [ ]   # scoped playback + transport
│  ├─ sections.py        [ ]   # heading detection (M5)
│  └─ scope.py           [ ]   # selection → timeline slice (M5)
├─ llm/
│  ├─ client.py          [ ]   # LiteLLM + Ollama, streaming, vision
│  ├─ explainer.py       [ ]   # vision figures / grid-summary tables
│  └─ director.py        [ ]   # opt-in LLM timing (--director)
├─ voice/
│  ├─ tts_engine.py      [ ]   # Piper / kokoro / espeak-ng / OpenAI / ElevenLabs
│  ├─ styles.py          [ ]   # 4 presets + override
│  └─ player.py          [ ]   # browser audio (default) / system audio
├─ server/
│  ├─ app.py             [ ]   # FastAPI/uvicorn, 127.0.0.1, open browser
│  ├─ api.py             [ ]   # REST: start/stop/pause/skip/select/model/voice/style
│  └─ stream.py          [ ]   # WebSocket/SSE events + audio framing
├─ viewer/
│  ├─ index.html         [ ]   # PDF.js render + selection UI
│  └─ transport.js       [ ]   # WS/SSE client + audio + controls
├─ config.py             [ ]
└─ cli.py               [ ]   # `ownvoicereader` entry: open viewer / --auto headless

pyproject.toml          [ ]   # console_scripts: ownvoicereader ; extras
tests/                  [ ]
```

> 📝 comment: _(any file you'd add / rename / drop)_

---

## 4. Unified `Block` shape (the "interchangeable" contract)

```
Block(id, kind: Text|Figure|Table, source: text|ocr,
      text: str, image: bytes|None, page: int, bbox,
      caption: str|None, table_grid: 2D list|None)
```
Text and OCR feed this **same** shape, so the reader never branches on origin.

> 📝 comment:

---

## 5. Risks & mitigations
- **Reading-order / columns** in multi-column papers → #1 correctness risk for "right moment." Mitigation: validate on the sample paper from §1.3 before M5.
- **Selection mapping** (PDF.js text-layer offsets → timeline indices) → map via **page + text-span anchors**, not raw char counts; test on samples.
- **Vision availability** → not every LLM sees images → A2 caption+context fallback is **mandatory**.
- **Audio latency / interruptibility** → streamed chunks + HTML5 audio (A9) keep pause/stop instant.
- **Dependency weight** → keep core light; gate heavy deps behind extras.
- **Explanation length** → explainer prompts force *spoken*, short output (no LaTeX/markdown).
- **Server hygiene** → `127.0.0.1` only, random port, local-only default (A13).

> 📝 comment: _(risks you'd add)_

---

## 6. Definition of done (v1 exit = M0–M6)

`ownvoicereader paper.pdf` opens a local viewer where you can read the **whole paper or
a selection** aloud in a chosen voice/style + LLM, get figure/table explanations
at first reference, and control playback — with **zero cloud required** when using
Ollama + local TTS/OCR.

> 📝 comment: _(what else must be true for "done"?)_

---

## 7. Sign-off

- [x] Section 1 decisions reviewed
- [x] §1.3 open questions answered
- [x] Sample paper chosen (§1.3): `test_pdfs/3799682.3840189.pdf` (FranKGraphBench, 6pp, 4-col)
- [x] M0 complete — viewer + server + UI upload (2026-11-24)
- [ ] Ready to start **M1** (text reader: extract → reading order → Piper TTS → streamed audio → controls)

> 📝 comment: _M0 DONE. Next: M1 text reader. A6 (live model/voice/style in UI), A9 (media-style forward/rollback controls), A10 (LLM section-detect fallback) noted for M1/M4/M5._
