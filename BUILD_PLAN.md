# OwnVoiceReader — Build Plan

> **Tagline:** *your model, your voice, your machine.*
> A lightweight **local web app** that renders an academic PDF, lets you read the
> **whole paper** (jumping between its **sections**),
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
- **Legend for milestones:** `M0` skeleton → `M6` packaging. v1 = M0–M5.
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
- [x ] **A3 Table detector** — **PyMuPDF** `find_tables()` + vision fallback for unstructured tables
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [x ] **A4 OCR engine** — **RapidOCR** default, Tesseract alt
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [x ] **A5 Interface** — **local web app** (Python server + browser **PDF.js** viewer)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A6 LLM client** — **`ollama` library** → local Ollama `/api/chat` (one interface; vision via M2.5, `lfm3-vl:2b`)
  - [ ] accept  - [ ] override →
  > 📝 comment: UI model picker to match machine; minimal installs; optional cloud API. Tokens from OS env, never stored (keeps secrets out of our hands).
- [ x] **A7 Explanation timing** — **first textual reference** default; `--director` opt-in (LLM places them)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A8 Reader styles** — 4 presets (`academic-calm`, `quick-scan`, `narrative`, `lecture`) + user override
  - [ x] accept  - [ ] override →
  > 📝 comment:

### 1.2 New — need your call
- [ x] **A9 Audio playback location** — **in-browser HTML5 `<audio>`** default; `--playback system` for headless/OS audio
  - [ x] accept  - [ ] override →
  > 📝 comment: allow keyboard/headphone controls for forward/rollback, not just play/pause
- [ x] **A10 Section detection** — v1 = **font-size + numbering heuristics** (PyMuPDF font metadata + `§3.1` regex); LLM refine optional flag
  - [ x] accept  - [ ] override →
  > 📝 comment: fallback to llm
- [x ] **A11 Packaging** — v1 = `pip install ownvoicereader` console script (`ownvoicereader`); true cross-platform **binary** (PyInstaller/Briefcase) deferred to M6
  - [ x] accept  - [ ] override →
  > 📝 comment: pip install yes; make setup.py; I handle distribution later
- [ x] **A12 Headless/CI** — keep `ownvoicereader paper.pdf --auto --no-browser` (full auto-read to system audio, no UI)
  - [ x] accept  - [ ] override →
  > 📝 comment:
- [ x] **A13 Server security** — bind **`127.0.0.1`** only, **fixed default port 6767** (0 = random free port, falls back to free if busy), `--local-only` forbids any cloud egress
  - [ x] accept  - [ ] override →
  > 📝 comment:

### 1.3 Open (your call — answer or skip)
- [ x] **Dev OS / environment** — Linux now; cross-platform later? _(Linux assumed)_
  > 📝 comment: cross-platform wherever Python runs — Windows + Mac
- [ x] **Local LLM now?** — will you have **Ollama** running for dev, or cloud-only first?
  > 📝 comment: local only first
- [ x] **First sample paper** — which PDF do we validate reading-order on?
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
> 📝 comment: DONE 2026-11-24. Renamed `ownvoice`→`ownvoicereader` (exclusivity); conda env + isolated pip config `.pip-ownvoicereader.conf` (bypasses broken NVIDIA index). **Primary flow = UI upload** (`POST /api/load`); CLI `paper.pdf` kept for convenience + headless `--auto`. `/pdf` = top-level route w/ HTTP range support. Provider secrets by name only. Hermetic tests. PDF.js vendored offline.


### M1 — Text reader  _status: [x] done_
- [x] `ingest/text_extractor.py` — text blocks + bboxes; flag sparse/empty pages
- [x] `ingest/reading_order.py` — merge blocks into one ordered sequence (2-col L→R, then down)
- [x] `voice/tts_engine.py` — **Piper** local engine (espeak-ng pivot; see note) + espeak fallback
- [x] `voice/player.py` — stream audio to **browser** (HTML5 `<audio>`); null/system for headless
- [x] `server/stream.py` — WebSocket: position, transcript, events, controls
- [x] `viewer/transport.js` — WS client + audio queue + highlight + transcript + controls
- [x] `reader/orchestrator.py` — read text aloud; **pause / resume / stop / skip / replay**
> 📝 comment: _DONE 2026-11-24. 135 cues / 31 blocks / 6pp on the 2-col sample; headers filtered, left col read before right. Voice = Piper `en_US-lessac-medium` — **espeak-ng unprovisionable** (conda/apt/pip all need a system binary we lack), so per decision went to Piper; abstraction still auto-selects espeak-ng if a binary appears. Audio = 16-bit mono WAV, one WS frame/cue, 1-cue lookahead → instant pause/stop. 17 tests pass; `transport.js` esprima-clean. _Smoke test found 2 defects (chunker drop, pause/skip) → fixed in **M1.5 C1**._

### M1.5 — Section-aware navigation + playback integrity  _status: [x] C1 + C2 + C3 done · confirmed by listen · M1.5 closed_
> Context: after the M1 audio smoke test two things surfaced — (1) **prose is
> skipped** while listening, and (2) the **skip / rollback** controls are
> **unpredictable** (they jump by an arbitrary 200-char cue, not by paper
> section). M1.5 fixes both and adds the **authors/institutions** front-matter
> block so the base reader is *reliable* before M2's figures/tables. It pulls
> forward the navigation-relevant slice (section detection) **without** the
> (now-removed) multi-scope selection UI, per A10.

**Diagnosis (root causes, confirmed on the 1-col Springer sample)**
- **Chunker drops whole sentences — THE single-run skip (CONFIRMED, ~40% word loss).**
  `_split_into_chunks` has a logic bug: when the running chunk is non-empty and the
  next sentence doesn't fit *and* that sentence is itself under the max, the code
  flushes the running chunk, resets it to empty, and — because the sentence isn't
  "over-long" — never assigns it back to the accumulator. **The sentence is
  silently dropped.** On the Springer front-matter block it loses 4 sentences
  (106 of 174 words), including the *opening and middle of the abstract* — exactly
  "authors → middle of abstract", in a single continuous run. Affects every paper,
  at essentially every chunk boundary. The one-line fix (carry the remainder into
  the accumulator) recovers 100%.
- **Pause/resume skip — CONFIRMED (secondary).** `transport.js` `pause()` runs
  `this.queue.length = 0`, discarding the 1-cue *lookahead* — but the server had
  already advanced its playhead `pos` past that cue. On resume the server emits the
  *next* cue, so the dropped lookahead is **never heard**.
- **Skip/rollback are cue-granular.** `skip()` advances `pos` by 1 *and* drops the
  lookahead (loses ~2 cues); `replay()` re-speaks `_last` (the lookahead cue) without
  resuming. A 200-char sentence-group is not a meaningful unit → "hard to predict".
- **TTS-failure NOTICE stall (latent).** A cue whose synthesis fails emits a `notice`
  with **no audio**; the client's `waitingForCue` flag never clears, stalling the
  stream until the user re-presses play.
- **Front matter is one merged block.** Title, authors, institutions, abstract and
  keywords all merge into a single full-width block (gap-based merge), so they aren't
  distinct sections; the author list also reappears as a running header (a *different*
  string), so it is spoken **twice** instead of once.

**Checkpoint 1 — playback integrity ("no prose is ever skipped, reliable pause/skip") — DONE 2026-11-24**
- [x] **Fix the chunker (headline).** `_split_into_chunks` now carries the remainder
      forward, so a sentence is **never dropped** (verified 174/174 words on the Springer
      front-matter block; previously ~40% were lost, incl. the abstract opening + middle).
      3 hermetic tests pin the invariant.
- [x] **Playback integrity.** `resume` / `start` are **state-only** on the server; `continue`
      is the sole forward producer, so the server's playhead is the single source of truth.
      The client **keeps its 1-cue lookahead across pause** (no `queue.length = 0`), so
      resume is lossless. `skip` / `replay` are now **client-side and predictable**: skip =
      advance exactly one cue; replay = re-hear the last *finished* cue (rollback). A
      TTS-failure `notice` now un-stalls the client (clears `waitingForCue`, pulls next).
- [x] **Tests + esprima.** 3 new chunker tests (no-sentence-loss, over-long word-break,
      Springer regression) → **20 tests pass**. `transport.js` esprima-clean. A faithful
      client/server simulation proves: continuous play = every cue once, in order; pause/
      resume = no gap / no duplicate; skip = +1 cue; replay = last finished cue. A live
      server read-through confirms the previously-vanishing abstract sentence is now spoken.

**Checkpoint 2 — section-aware navigation — DONE 2026-11-25 (awaiting your listen)**
> You chose: skip granularity = **section + subsection**; announce section names = **ON**.
> Both confirmed on a live WS read-through over the ACM 2-col sample.
- [x] `reader/sections.py` — `detect_sections` builds a flat
       list of **sections** over the *spoken* blocks, each with a `block_start` (and
       `cue_start`/`cue_end` filled by the orchestrator): `title`, `authors`+`affiliations`,
       `abstract`, `keywords`, `ccs`, numbered/bold **section / subsection** headings,
       `references`, `acknowledgments` (A10 heuristics: font prominence + bold + `^N(\.N)*`
       / `Abstract` / `CCS Concepts` / `References` / `Acknowledg…`). LLM refine stays opt-in.
- [x] **Carry font info into `Block`** — `Block`/`text_block` gained `font_size` /
       `is_bold`; `reading_order._merge_into_blocks` now propagates the dominant font
       (max line size) + bold (longest line), and splits blocks at a **font transition**
       (`_FONT_SPLIT_PT=1.5`) or a **marker** line (`_MARKER_RE`) so front matter +
       headings don't merge into the following body.
- [x] **Front-matter ordering + author de-dup** — front matter reads title → authors →
       abstract → keywords (the split makes each its own block); the author running-header
       *variant* is dropped by **role+position** (`_is_running_header`: page>0, top band,
       not a genuine heading), so authors are spoken **once** even when the repeated line
       differs from the front-matter one.
- [x] **Section-aware skip / rollback (A9)** — new server commands `next_section` /
       `prev_section` move the playhead to section starts by cue index; `⏭`/`⏮` now jump by
       section (+ a live cue counter + a "now reading: § <section>" chip). A fine per-cue
       `skip`/`replay` stays client-side (lossless) and is kept as a fallback.
- [x] **Announce section names (ON)** — on a *jump* the first cue leads with "Section 3,
       Method" (`_lead_in_spoken` replaces a standalone heading cue to avoid "Section 3,
       Method 3. Method"); front-matter kinds are not announced (they name themselves).
- [x] **Tests** — 12 new hermetic tests in `tests/test_sections.py` (front-matter + body
       detection, variant-author de-dup, the `allow_prominent`/`seen_numbered_section` gate
       that stops 2-col author names becoming sections, cue-range contiguity, next/prev
       navigation by cue index, the spoken lead-in) → **32 tests pass**; `transport.js`
       esprima-clean.
- [x] **transport.js + the "now reading" bar** — the cue counter now updates **live on
       every event** (and on a section-jump `state` frame, so a jump shows the paragraph it
       skipped); a `#now-section` chip shows the current section; a section jump clears the
       stale lookahead and holds cues until the jump's `state` frame resyncs. `esprima`-clean.

**Checkpoint 3 — fine jump/rollback + on-the-fly document swap — DONE 2026-11-25 (awaiting your listen)**
> Three refinements you asked for after the C2 listen.
- [x] **Fine paragraph jump / rollback (the buttons C2 had hidden).** C2 rewired
       `⏭`/`⏮` to *section* jumps and left the per-cue `skip`/`replay` unwired. Now both
       granularities are visible: `⏩`/`⏪` (fine, one cue ≈ a paragraph) sit between the
       coarse `⏭`/`⏮` (section). They share one server-side mechanism with the section
       jumps (`_jump` + a `state` reply), so the live counter + "now reading" chip resync
       the same way.
- [x] **`⏪` rolls back *one cue*, not to the section start** (your specific point). New
       server commands `next_cue` / `prev_cue` = `pos ± 1`; the coarse `prev_section`
       still snaps to the current section's start when you're mid-section, but the fine
       `prev_cue` always moves exactly one paragraph — so from the start of a cue it
       re-hears the *previous* paragraph instead of jumping to the section start. A fine
       jump that *lands* on a section start still speaks the lead-in ("Section 3, Results").
- [x] **"Change PDF…" button (on-the-fly document swap, no server restart).** The server
       already supported it (`POST /api/load` + `onDocument`, used by the landing drop
       zone); C3 exposes it in the reading bar. Clicking it opens the file picker, uploads
       to `POST /api/load`, and `onDocument` tears down the old reader/WS + loads the new
       PDF + re-renders — all in the running server. Verified live: swapping ACM → Springer
       mid-session makes a fresh WS handshake report the new document's `cue_count` (253→445).
- [x] **Double-click on `⏮` skips a *whole* section** (a follow-on you asked for after the
        C3 listen). A single `⏮` still re-hears the current section; a *double-click* lands at
        the start of the section *before* the current one — a real "skip a chapter", not a
        re-hear. It is a genuinely new server command `prev_section_skip` (= start of the
        *previous* section, clamped to 0 at the first), not a reuse of the re-hear path. A short
        timer on the client detects the double so the two clicks that precede a `dblclick` do not
        each fire a jump. (An earlier attempt put this on the fine button lazily; that was
        rolled back — `git reset` of that commit — and done properly here.)
- [x] **Tests** — 3 more hermetic tests in `tests/test_sections.py` (`prev_section_skip` lands on
        the *previous* section start, distinct from the single-click re-hear; a skip landing on a
        numbered section announces its name; it clamps to 0 at the first) → **39 tests pass**;
        `transport.js` esprima-clean; live WS read-through confirms `prev_section_skip` goes
        further back than `prev_section` (from mid "Authors & institutions" pos 3: re-hear → pos 1,
        skip → pos 0 / the title).

> 📝 comment: _All 3 M1.5 checkpoints built + verified + **user-confirmed**. Follow-on:
> **double-click `⏮` skips a whole section back** (single click re-hears) — a new
> `prev_section_skip` command. 39 tests pass; live WS confirms the skip goes further
> back. **M1.5 closed** → M2. Hard-refresh `Ctrl+Shift+R` for the JS change._

### M2 — Figures & tables  _status: [~] C1 + C4 + C2 + M2.5 (vision + status badge) done · C3 optional_
- [x] `ingest/figure_table.py` — **PyMuPDF** `find_tables()` (tables→grid+bbox) + `get_pixmap(clip=…)` (figures→PNG+bbox); de-dup + min-size; append FIGURE/TABLE blocks; `render_region` (M2.5)
- [x] `reader/references.py` — detect `Fig. 3 / Figure 3 / Fig. 3a / Table 2(b) / Tbl. 2 / TABLE I`; build reference index; **`caption_target` now trims a merged caption+body block to its caption sentence(s)** (C2)
- [x] `reader/timeline.py` — **"right-moment"** engine (A7): fire an `EXPLAIN` at each figure/table's *first prose reference*; composer prefers a **vision** phrase, else the A2 caption+context base
- [x] orchestrator emits + speaks `EXPLAIN` events (pending-queue, navigation-safe); **starts a background vision-precompute thread** (M2.5)
- [x] `llm/explainer.py` — vision **figure** description + **table meaning** (rendered region + grid text); *spoken*, short, no LaTeX/markdown (M2.5)
- [x] `llm/client.py` — **`ollama` library** → local Ollama `/api/chat` (no litellm, no httpx); streaming; vision inputs; user-configurable host (M2.5)
> 📝 comment: _**C1 + C4 done + verified.** `reader/references.py` builds a `ReferenceIndex`
> — one entry per figure/table: **caption**, **first prose reference** (A7 "right moment"),
> mention count. `reader/timeline.py` maps each to the cue that *speaks* it; spoken text =
> **caption + context** (A2 base, LLM-free) if a caption exists, else the reference sentence;
> M2.5 layers vision. Orchestrator emits `EXPLAIN` as a **pending follow-on** (queued after the
> reference cue, *not* a new cue) → navigation untouched.
>
> _**C2 done 2026-11-25.** `ingest/figure_table.py` populates **TABLE** (`find_tables()` grid +
> bbox) + **FIGURE** (`get_pixmap(clip=…)` PNG + bbox) blocks, de-dup'd + min-size-filtered;
> runs after `order_lines`. `caption_target` trims a merged caption+body block to its caption
> sentence(s), closing C4's "…is referenced here:" gap.
>
> _**Calibration:** captions on every sample — **SBC 1→6/6, IEEE 5→9/10, Springer 0→3/15, ACM
> 0→2/4**; EXPLAINs use real captions. **8 + 4 → 81 tests.** **Limits → C3/M2.5:** table
> captions still include row text (noisy); a few captionless entries (`fig:3a` sub-panels,
> IEEE `TABLE I`) fall back to the reference sentence. **Decision resolved:** vision optional
> (M2.5); A2 base shipped (LLM-free, testable model-less); `lfm3-vl:2b` wired in M2.5._

### M2.5 — Vision LLM through ollama python library  _status: [x] done · listenable_
- [x] using `lfm3-vl:2b` as the default model add the optional vision description into the caption + context design whenever this or another config vision model is available on that machine. make sure
- [x] make sure the vlm can also explain meaning from tables
- [x] try and calculate vlm explanations in the background (when indexing) and serve them at the appropriate time
- [x] use client and then chat so that a different ollama server can be configured by the user
- [x] update pyproject.toml without litellm and pdfplumber. also move piper-tts with the voice installation into the core dependencies. ollama should be in vision.
> 📝 comment: _**pyproject done 2026-11-25:** dropped `litellm`/`httpx`/`pdfplumber`;
> `piper-tts` → **core**; `ollama>=0.6` → new **`vision`** extra (M2.5 client talks to a
> local Ollama server via the `ollama` library). Stale `litellm` refs cleaned. 69 tests pass._
>
> _**M2.5 done + verified 2026-11-25.** New `llm/client.py` + `llm/explainer.py`.
> - **Client (A6):** wraps the **`ollama`** lib (`OllamaClient`) behind `LlmClient` (mirrors
>   `voice/tts_engine.py`): `chat`/`describe`/`list_models`/`has_model`/`available`.
>   `NullLlmClient` = graceful "off" → degrades to A2 base, never crashes. Host user-configurable
>   via `OLLAMA_HOST`/`--llm` from OS env, never persisted; lazy import (core install still loads).
> - **Explainer:** `link_media` pairs each figure/table **block** with its caption target
>   (C2-deferred type-aware match, needed since vision needs pixels). Two invariants → **each
>   figure/table its own description**: (1) **same kind** — FIGURE↔`fig:`, TABLE↔`table:`;
>   (2) **2-D proximity** — nearest caption by *x+y*, not y-only (once swapped a left-col table
>   with a right-col figure). Cross-page/captionless → A2. `describe` sends the **figure image**
>   to the VLM and, for a **table**, the *rendered region + grid text* (explains *meaning*);
>   `precompute` fills `ExplainItem.vision`, recomposing to **prefer vision over A2**.
> - **Background (A7):** orchestrator runs `precompute` for the whole paper in a **daemon thread**
>   at load; if vision off/slow, A2 caption+context is spoken — read never silent/blocked. Vision
>   phrase tidied (`_clean_vision`) to drop "this figure shows…" lead-ins.
> - **Wiring + badge:** `get_llm_client(state)` (cached, like `get_tts_engine`) feeds web +
>   headless. **Vision badge** (read-only chip, like Piper's, via `_vision_info` in WS `hello`)
>   shows the resolved model **green when reachable / red "off" when not**; hello *probes*
>   reachability in a thread; `/api/info` cache-only.
> - **Calibration (real `lfm3-vl:2b` listenable):** **SBC 4/6, IEEE 9/10, ACM 2/2, Springer 2/3**
>   enhanced; rest → clean A2 base. Clip at `/tmp/opencode/m25_vision_demo.wav`.
> - **Tests:** 15 `test_llm_client` + 23 `test_llm_explainer` + 6 `test_server_api` → **125 pass.**
>
> _**Limits → later:** captionless/cross-page figures stay on A2 (caption clean → text fine);
> vision thread best-effort (no hot-path wait). Captionless tables (IEEE `TABLE I`) use the A2
> reference sentence for context, still get a vision description when linked._
>
> _**M2.5 follow-up — multi-cue explanations + spoken markers (2026-11-26).** A single cue
> is too short for a figure/table, so an explanation now **spans several cues**: its composed
> text is split into TTS-sized chunks **at fire time** (a vision description that lands in the
> background is spoken in full), each its own `EXPLAIN` event + WAV. The group is **bracketed by
> spoken markers** — a start ("Now, a closer look at this figure/table.") and an end ("Back to the
> text.", spoken **only when prose follows**) — so a listener who can't see the page knows when
> the reader diverts and when it returns. Markers are tagged `meta.phase`
> ("start"/"body"/"end"); the viewer styles them (italic body, accented markers). A group shares
> **one** start + **one** end (an interleaved 2-column cue can reference several at once).
> `orch.explain_markers` toggles the bookends (default on). The headless summary now reports
> *spoken units* vs *text cues* (e.g. **266 / 253** on ACM). +7 tests → **132 pass.**_

### M3 — OCR (text/OCR interchangeable)  _status: [x] done · listenable_
- [x] `ingest/ocr.py` — **RapidOCR** fallback for pages/regions without a text layer
- [x] OCR emits the **same `Block` shape** as `text_extractor`
- [x] **parity test**: a scanned paper reads identically to a text-based one
- [ ] maybe use the OCR to help confirm the table and figure captions limits
> 📝 comment: _**M3 done + verified 2026-11-26.** New `ingest/ocr.py` — the OCR side of the
> "interchangeable contract" (§4): a scanned page reads the same as a typed one, with **no
> downstream branching on origin**.
> - **Shape:** the OCR path emits the **same** `Line`/`Block` shape as the text layer, tagged
>   `source = BlockSource.OCR` (new on `Line` + propagated through `reading_order` → `Block`).
>   OCR leaves `font_size`/`is_bold` at their defaults (a scan has no font metadata) — the shape
>   is identical; only the *cue* is honestly absent.
> - **Pipeline:** `flag_sparse_pages` (< 30 chars) already marks the OCR candidates; for each we
>   rasterize (`get_pixmap`, 300 dpi) and run an `OcrEngine`, mapping each line's pixel box back
>   to PDF user space (`pdf_x = rect.x0 + px/scale`) and merging the `Line`s with the text layer,
>   so one `reading_order` pass reads scanned + typed pages together.
> - **Engine abstraction** (mirrors `tts_engine`/`llm/client`): `RapidOcrEngine` (default, A4 —
>   the `rapidocr` lib, fully-local, models ship with the package), `TesseractEngine` (alt —
>   `pytesseract` over the system binary, PSM 6), and a graceful `NullOcrEngine` (the "OCR off"
>   state: reports unavailable, yields nothing, the reader degrades to text-only — never crashes).
>   `build_ocr_engine` picks the best available (RapidOCR → Tesseract → null); `OWNVOICEREADER_OCR_ENGINE`
>   / `OWNVOICEREADER_OCR_DPI` override.
> - **Wiring:** `build_document(ocr=…)` OCRs sparse pages and merges them; **skips figure/table
>   extraction on fully-scanned pages** (a scan's single raster is the whole page, not a figure).
>   Server: `get_ocr_engine(state)` caches one engine (the model loads lazily on
>   the first *sparse* page — building the engine is import-only, ~1 ms);
>   `/api/info` reports an `ocr` status. A text-only PDF pays nothing of note —
>   the engine is built cheaply but its model is never loaded.
> - **`pyproject`:** `ocr` extra now `rapidocr>=3.0` (+ `pytesseract`) — the old
>   `rapidocr-onnxruntime` line caps at Python <3.13, so the maintained `rapidocr` package is used.
> - **Calibration (real `rapidocr` on the new CEUR sample pair):** the typed `1-column_ceur.pdf`
>   reads 23,564 chars / 53 blocks; its scanned twin `1-column_ceur_scanned.pdf` is **OCR'd to
>   22,893 chars / 34 blocks (~97% recovery)** — every block `source=ocr`. Listenable proof at
>   `/tmp/opencode/m3_scanned_ocr.wav` (8 OCR'd cues, Piper). **Parity test**
>   (`test_parity_scanned_reads_like_text`) asserts the scan recovers the typed twin's content
>   (skips itself if the `[ocr]` extra or the sample is absent). **+14 tests → 146 pass.**
>
> _**Limits → later:** OCR'd lines have no font cues (section detection is weaker on scans — a known
> limitation); a text-less page with OCR *off* still surfaces its raster as a display figure (the
> spoken path is unaffected); the "use OCR to confirm caption limits" stretch goal is deferred._

### M4 — Config & models  _status: [x] done 2026-11-26_
- [x] `config.py` — layering: defaults → `~/.ownvoicereader/config.yaml` → env `OWNVOICEREADER_*` → CLI flags
- [x] live change of **model / voice / style** from the viewer UI
- [x] `--local-only` (no cloud egress) + Ollama-first path
- [x] **Save-settings button** (new): the viewer writes the current model/voice/style/local-only to `config.yaml`
- [x] **Ollama host control** (new): a host input + a "⟳ Refresh models" button in the settings group —
      re-point the vision LLM at a *different* Ollama server and re-probe the model list live
      (a `set_ollama_host` WS frame → a `vision` reply); the host is a saved setting too.
> 📝 comment: _M4 done. The config file is the user's **saved** settings (the
> model/voice/style they pick in the UI, written by the "💾 Save" button) and
> sits *below* env + CLI in the layering — a saved choice applies on next launch,
> but an exported `OWNVOICEREADER_*` var or a flag still wins. The file is
> optional + best-effort: a missing or malformed `config.yaml` degrades to the
> lower layer, never a crash. `OWNVOICEREADER_CONFIG` overrides its location.
>
> **Live switching** is via WS frames: `set_voice`/`set_style` (already M2.5) and
> the new `set_model`/`set_local_only` — each updates the orchestrator (and the
> shared config, so "Save" persists it) and a `state` reply resyncs the UI.
> **Scope note:** a model switch is *live* for the next document load / re-read —
> the open document's vision is pre-computed in the background, so the new model
> fully takes effect on the next load (noted in the picker's tooltip). The badge
> shows *status*; the picker in the settings group is the *control*.
>
> **What the file holds** (durable settings only — `pdf_path` is per-run and never
> saved): `host`, `port`, `open_browser`, `local_only`,
> `playback`, `director`, `llm_provider`, `llm_model`, `ollama_host`, `voice`,
> `style`. A host *endpoint* (not a credential) is persisted; API keys are never
> written. Write
> is atomic (temp + rename). `POST /api/save` (the button) →
> `save_settings` → `config.save_config`; `/api/info` reports the current
> `settings` + `vision.models` (cache-only) for the viewer. +25 tests → 172 pass.
>
> **Test hardening** (no new feature, deeper coverage of the M4 surface): the
> `OllamaClient` model-list cache (`list_cached_models` empty-before-probe / cached
> after / cached even when the configured model is absent / `refresh` clears it),
> orchestrator robustness (`set_style` on an unknown name degrades to the default,
> `status` with no shared config), the WS `set_model`/`set_local_only` edge branches
> (the `"model"` key alias, the default-`True` when omitted) and the `set_voice`/
> `set_style` `state` resync, plus `_settings_payload` fallbacks (style → default
> preset, model → `DEFAULT_MODEL`) + an empty-body `POST /api/save` + config edge
> cases (unset config path, non-numeric `port`, bool coercion from int). +15 → 189.
>
> **Ollama host control** (a "⟳ Refresh models" button + a host input in the settings
> group, requested after M4's first pass): the user can point the vision LLM at a
> *different* local Ollama server from the UI. The new `set_ollama_host` WS frame
> re-points the *shared* client (so the next probe / document load reaches the new
> server) and re-probes, replying with a `vision` frame (fresh model list + the host
> the reader now points at) so the picker + badge refresh *without a reload*; the host
> is also a saved setting (`config.ollama_host`, persisted by "Save", overridable by
> `OWNVOICEREADER_OLLAMA_HOST`). The host input is free-text and **typeable from the
> start**; a user-typed value is flagged so a later `hello` pre-fill (e.g. after a
> "Change PDF" reconnect) never clobbers it.
>
> **A6 guard (hard rule, user-stated): a token/credential is *never* saved — only the
> host *endpoint* is durable.** `tests/test_config.py::test_no_credentials_are_ever_persisted`
> pins it: the credential env-vars (`PROVIDER_ENV_VARS`) are disjoint from the persisted
> fields (`PERSIST_FIELDS`), and a save with *every* credential exported writes none to
> disk (while the host still is). +13 tests → **202**, +1 guard → **203 pass.**_

### M5 — UI revamp  _status: [~] C1–C6 done · C7 (unify icon pack) next · in progress_
- [x] make sure the cue being read is the one in now reading at all times as it changes to the next cue to soon in previous versions
  - _✅ **C1 done.** "now reading" (text / section chip / cue counter / transcript current-line) + auto page-scroll all key off the cue being *heard*, not the one the server merely produced — the lookahead buffer was making it jump to the next cue too soon. The PDF **overlay highlight was scrapped** (it went off-track on section/block jumps); C3/C4 add a transcript-based follow-along instead. `pump()` wires the critical path first (`onended`→`play()`) and runs the cosmetic now-reading update *guarded* so it can never stall the audio.
- [x] make a light UI with high contrast as default but give a dark high contrast option. also add a dracula theme
  - _✅ **C2 done.** One header button (top-right) cycles **light (high-contrast — the default) → dark (high-contrast) → Dracula** and shows the *current* scheme's icon (🌞 / 🌙 / 🧛). The three schemes are CSS-variable blocks keyed off `<html data-theme>`; a new `--on-accent` var drives text on accent fills so `#open-btn` stays legible in every theme. The pick is saved to `localStorage` (`ownvoicereader.theme`) and a tiny inline `<head>` script applies it *before first paint* so a saved Dracula doesn't flash as light on reload. Status badges + inline `code` (previously hardcoded dark) are now theme-adaptive so the light default reads at high contrast. The white PDF page is left white in all themes (it's the paper)._
- [x] change to the now reading and transcript parts of the UI: text (particularly now reading) should have high contrast an larger for low-vision users (primary target of this tool). previous cues and forward cues should also show up similar the lyrics UI in Apple Music instead of the block system in place for previous milestones. show the now reading in the middle with a large font and one previous cue and the next in the bottom with smaller font.
  - _✅ **C3 done.** The now-reading bar + the toggleable transcript block are now **one lyrics-style follow-along panel** (`#lyrics`): the cue being *heard* is enlarged (24px) + high-contrast + **centred in the middle**, with the neighbouring cues dimmed + smaller (15px) above/below — Apple-Music style, replacing the old block system. **Previous cues are capped at 3** (the first-said cue drops out of the DOM once a 4th would appear), so the window stays tight (≈3 prev · current · a few next) rather than growing. The M2.5 explanation line styles (`notice` / `explain` / `explain-marker`) carry over._
- [x] transcript block can stay to the right of the paper view when screen space is afforded. when no space on the side is available make it show one line in the bottom with no option to make it larger.
  - _✅ **C4 done.** The panel sits to the **right of the PDF** as a rounded, slightly-elevated **420px card** (thicker right margin so it floats centrally rather than hugging the edge). As the window narrows the **margins collapse first** — thick right gap → 16px at ≤1120px, all margins → 0 (flush) at ≤1000px — and only at **≤900px** does it drop to a **single full-width bottom line** (head + list hidden, no enlarge option). Pure CSS media queries — no JS layout logic._
- [x] **settings side bar (responsive; C6→C5).** The model / host / voice / save controls move out of the bottom bar into a **responsive settings side bar** — a **420px card, same aesthetic as the now-reading panel**. It **shrinks its margins and collapses *before* the now-reading**, reopening via a **burger icon**. Inside, in order: (1) a **drag-and-drop area + an "Open a PDF…" button** (like the landing screen), (2) the **voice + style dropdowns**, (3) when vision is on, the **Ollama host textbox + a Refresh button on its right**, then the **list of Ollama models**. The **bottom bar keeps only the media + PDF-viewer controls**. At the **final collapse** the now-reading **stays** and the **PDF viewer collapses entirely**.
  - _✅ **C5 done.** The controls moved out of the bottom bar into a **responsive settings side bar** — a **420px card of the same rounded/elevated aesthetic** as the now-reading panel, on the **left** of the PDF (a 3-column row: **settings · PDF · now-reading**, the latter widened to **500px**). Contents, in order: (1) a compact **drag-and-drop + "Open a PDF…"** (reusing the landing's hidden file input), (2) **voice + reader-style** selects, (3) the **vision group** — Ollama host input + **⟳ Refresh** on one row, then the **Ollama models** list (kept **always visible** so the host can be pointed at before vision is up), (4) **local-only** + **💾 Save**. **Collapse sequence** as the window narrows: thick inner margins shrink first (**≤1280px**) → the **settings card collapses to a ☰ burger** slide-over anchored *below the header* (**≤1080px**, *before* the now-reading) → the **PDF collapses last** (**≤900px**) while the **now-reading stays** full-width. At that final collapse the PDF and now-reading are **mutually exclusive** via a **single centered 📄/📖 cycler** (icon reflects the panel that's up; a click swaps to the other, so the page fills the space instead of being squeezed). The **footer keeps only media + PDF-viewer controls**. General font bump for the low-vision target (base 14→16px; config-bar labels/selects 12/13→14/15px)._
- [x] show markdown version of table and the figure being explained whenever that's happening on the new transcript/now reading UI. the image and table view should show the cut sent to the VLM below the text and disappear once it goes back to prose.
  - _✅ **C6 done.** While the reader diverts to explain a figure/table, the **now-reading panel shows the "cut sent to the VLM" below its head**: a **figure** as its cropped image, a **table** as its cell grid rendered as a markdown-style HTML table. **Server** (`orchestrator`): each figure/table *target* is linked to its media block — reusing M2.5's `link_media`, computed **unconditionally** (a cheap block scan, no LLM) so the cut shows *with or without vision* — and `meta.media` (`{kind, caption, image?/grid?}`) is attached to the `EXPLAIN` event: a figure's cropped PNG as a **base64 data URI** (self-contained in the frame, no extra fetch), a table's **`grid`** as the "markdown version of the table". **Viewer** (`#lyrics-media` card, below the head, capped at ~45% of the panel so the transcript scrolls beneath it): shown while the explanation plays, **deduped by target** (the many body chunks of one long description rebuild the media once, not per chunk), and **hidden via the global `.hidden`** the moment the next unit carries no media — the "Back to the text." marker or the next prose cue — so it **disappears once prose resumes**; also cleared on stop / new document. Table cells are set via `textContent` (never `innerHTML`) so PDF text can't inject markup. +3 tests → **206**._
- [ ] unify icon style with a single minimalist icon pack (maybe give me some options). make sure buttons next to each other are the same and the local-only toggle matches the style and is near the vision block. use distinctive icons from passing the pdf pages to the play button in the audio. (maybe fontawesome icons?)
> 📝 comment:

### M6 — Packaging (later, post-v1)  _status: [ ] not started_
> **Hardening first, then the binary.** The old *Hardening* milestone was folded
> here: its pending items lead the packaging work. The two already-decided items are
> not carried as tasks — graceful LLM-down / TTS-down degradation already shipped with
> M2.5 / M1, and the **resume checkpoint (A14) was scrapped** as not needed: it would
> add complications for a local reader.
- [ ] **hardening:** error handling + unit tests across ingest / references / timeline
- [ ] **hardening:** a guided install doc for the core + each optional extra
- [ ] **PyInstaller / Briefcase** cross-platform binary (`.exe` / AppImage / `.dmg`)
- [ ] final UI should open on its own window but keep mostly web usage (similar to discord)
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
│  ├─ orchestrator.py    [ ]   # cues + playback + transport
│  └─ sections.py        [x]   # section-aware navigation (M1.5, done)
├─ llm/
│  ├─ client.py          [ ]   # ollama library, streaming, vision
│  ├─ explainer.py       [ ]   # vision figures / grid-summary tables
│  └─ director.py        [ ]   # opt-in LLM timing (--director)
├─ voice/
│  ├─ tts_engine.py      [ ]   # Piper / kokoro / espeak-ng / OpenAI / ElevenLabs
│  ├─ styles.py          [ ]   # 4 presets + override
│  └─ player.py          [ ]   # browser audio (default) / system audio
├─ server/
│  ├─ app.py             [ ]   # FastAPI/uvicorn, 127.0.0.1, open browser
│  ├─ api.py             [ ]   # REST: start/stop/pause/skip/model/voice/style
│  └─ stream.py          [ ]   # WebSocket/SSE events + audio framing
├─ viewer/
│  ├─ index.html         [ ]   # PDF.js render + controls
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
- **Reading-order / columns** in multi-column papers → #1 correctness risk for "right moment." Mitigation: validate on the sample paper from §1.3.
- **Vision availability** → not every LLM sees images → A2 caption+context fallback is **mandatory**.
- **Audio latency / interruptibility** → streamed chunks + HTML5 audio (A9) keep pause/stop instant.
- **Dependency weight** → keep core light; gate heavy deps behind extras.
- **Explanation length** → explainer prompts force *spoken*, short output (no LaTeX/markdown).
- **Server hygiene** → `127.0.0.1` only, fixed default port 6767 (0 = random), local-only default (A13).

> 📝 comment: _(risks you'd add)_

---

## 6. Definition of done (v1 exit = M0–M5)

`ownvoicereader paper.pdf` opens a local viewer where you can read the **whole paper**
(jumping between its sections) aloud in a chosen voice/style + LLM, get figure/table
explanations
at first reference, and control playback — with **zero cloud required** when using
Ollama + local TTS/OCR.

> 📝 comment: _(what else must be true for "done"?)_

---

## 7. Sign-off

- [x] Section 1 decisions reviewed
- [x] §1.3 open questions answered
- [x] Sample paper chosen (§1.3): `test_pdfs/3799682.3840189.pdf` (FranKGraphBench, 6pp, 4-col)
- [x] M0 complete — viewer + server + UI upload (2026-11-24)
- [x] M1 complete — text reader: 2-col extract → reading order → Piper TTS → streamed WAV audio → pause/resume/stop/skip/replay (2026-11-24)
- [~] **M1.5 Checkpoint 1 (playback integrity) done — awaiting your listen** (2026-11-24). Fixed the **chunker sentence-drop** (~40% word loss on the Springer front-matter block, incl. the abstract opening+middle), the **pause/resume lookahead drop**, **cue-granular skip/replay** (now client-side, predictable), and a latent **TTS-failure stall**. 20 tests pass; live read-through confirms the abstract sentence is now spoken. **Checkpoint 2 (section-aware navigation + spoken section names) is planned and awaiting your go-ahead.**
- [ ] Ready to start **M2** (figures & tables + LLM explainer + first-reference `EXPLAIN` timeline)

> 📝 comment: _M1 done (smoke test → M1.5 defects fixed). espeak-ng→Piper pivot. A6: voice+style in M1, LLM model select in M4; A9 in M1; A10 → M2/M4._
