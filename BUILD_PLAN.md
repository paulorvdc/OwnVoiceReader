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
- [ x] **A13 Server security** — bind **`127.0.0.1`** only, **fixed default port 6767** (0 = random free port, falls back to free if busy), `--local-only` forbids any cloud egress
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


### M1 — Text reader  _status: [x] done_
- [x] `ingest/text_extractor.py` — text blocks + bboxes; flag sparse/empty pages
- [x] `ingest/reading_order.py` — merge blocks into one ordered sequence (2-col L→R, then down)
- [x] `voice/tts_engine.py` — **Piper** local engine (espeak-ng pivot; see note) + espeak fallback
- [x] `voice/player.py` — stream audio to **browser** (HTML5 `<audio>`); null/system for headless
- [x] `server/stream.py` — WebSocket: position, transcript, events, controls
- [x] `viewer/transport.js` — WS client + audio queue + highlight + transcript + controls
- [x] `reader/orchestrator.py` — read text aloud; **pause / resume / stop / skip / replay**
> 📝 comment: _DONE (2026-11-24). 135 cues / 31 blocks / 6 pp on the 2-col sample, page numbers + running headers filtered, left column fully read before right. Piper `en_US-lessac-medium` (22050 Hz) as the local voice — **espeak-ng could not be provisioned** (conda-forge resolve fails, apt needs sudo, the pip `espeakng` pkg is only a subprocess wrapper needing the binary+voice data, none present), so per the decision "espeak-ng first, then Piper" we went straight to Piper; the `TtsEngine` abstraction still auto-selects espeak-ng if a binary ever appears. Audio is self-describing 16-bit mono WAV over one binary WS frame per cue; browser pulls the next cue (`continue`) with 1-cue lookahead → instant pause/stop. 17 hermetic tests pass (incl. 2-col PyMuPDF fixture); `transport.js` esprima-clean. _Audio smoke test done (2026-11-24): it surfaced two defects — a **chunker sentence-drop** and **pause/skip** that skip or jump unpredictably — now fixed in **M1.5 Checkpoint 1** (20 tests, live read-through confirms the abstract sentence is spoken)._

### M1.5 — Section-aware navigation + playback integrity  _status: [x] C1 + C2 + C3 done · confirmed by listen · M1.5 closed_
> Context: after the M1 audio smoke test two things surfaced — (1) **prose is
> skipped** while listening, and (2) the **skip / rollback** controls are
> **unpredictable** (they jump by an arbitrary 200-char cue, not by paper
> section). M1.5 fixes both and adds the **authors/institutions** front-matter
> block so the base reader is *reliable* before M2's figures/tables. It pulls
> forward the navigation-relevant slice of M5 (section detection) **without** the
> full selection UI, per A10.

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
- [x] `reader/sections.py` (pulled forward from M5) — `detect_sections` builds a flat
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

> 📝 comment: _All three M1.5 checkpoints are built + verified, and **confirmed by the user
> listen** (all three C3 points work). One follow-on refinement: **double-click on `⏮` now skips
> a whole section back** (single click still re-hears the current section) — a new
> `prev_section_skip` command, not a lazy reuse of the re-hear path. 39 tests pass; the live WS
> read-through confirms the skip goes further back than the single-click rollback. **M1.5 is
> closed.** Moving to M2 (figures/tables). Hard-refresh `Ctrl+Shift+R` to pick up the JS change
> when you next open the reader._

### M2 — Figures & tables  _status: [~] C1 + C4 + C2 done · C3 (grid summary) + M2.5 (vision) next_
- [x] `ingest/figure_table.py` — **PyMuPDF** `find_tables()` (tables→grid+bbox) + `get_pixmap(clip=…)` (figures→PNG+bbox); de-dup + min-size; append FIGURE/TABLE blocks
- [x] `reader/references.py` — detect `Fig. 3 / Figure 3 / Fig. 3a / Table 2(b) / Tbl. 2 / TABLE I`; build reference index; **`caption_target` now trims a merged caption+body block to its caption sentence(s)** (C2)
- [x] `reader/timeline.py` — **"right-moment"** engine (A7): fire an `EXPLAIN` at each figure/table's *first prose reference*; caption+context composer (A2 base, LLM-free)
- [x] orchestrator emits + speaks `EXPLAIN` events (pending-queue, navigation-safe)
- [ ] `llm/explainer.py` — table→**grid summary**; *spoken*, short, no LaTeX/markdown (figure vision is M2.5)
- [ ] `llm/client.py` — **`ollama` library** → local Ollama `/api/chat` (no litellm, no httpx); streaming; vision inputs (M2.5)
> 📝 comment: _**C1 (reference detection + index) and C4 ("right-moment" engine)
> are done + verified.** `reader/references.py` builds a `ReferenceIndex` — one
> entry per figure/table with its **caption**, the reading-order position of its
> **first prose reference** (the A7 "right moment"), and a mention count.
> `reader/timeline.py` maps each entry to the cue that *speaks* that reference and
> composes the spoken explanation: **caption + context** (A2 base, LLM-free) when a
> caption exists, else the reference sentence; M2.5 layers vision on top. The
> orchestrator emits the `EXPLAIN` as a **pending follow-on** (queued right after
> the reference cue, *not* a new cue) so section/cue navigation is untouched.
>
> _**C2 (figure/table extraction) is done (2026-11-25).** `ingest/figure_table.py`
> (new) populates `doc.blocks` with **TABLE** blocks (a `find_tables()` cell grid +
> bbox) and **FIGURE** blocks (a `get_pixmap(clip=Rect(bbox), dpi=150)` PNG + bbox),
> de-duplicated by overlap and min-size-filtered so a page of icons isn't 23
> "figures." It runs after `order_lines` in `build_document`; the spoken path is
> untouched (cue-building and `build_index` skip non-text blocks). The caption gap
> from C4 is **closed**: `caption_target` no longer rejects a caption merged with its
> body — it trims the merged block to its first sentence(s) (so the body never leaks
> into the stored caption), fixing the "…is referenced here:" fallback.
>
> _**Calibration (all 4 samples, real Piper listenable):** captions now detected on
> every sample — **SBC 1→6/6, IEEE 5→9/10, Springer 0→3/15, ACM 0→2/4.** The spoken
> EXPLAINs now use real captions, e.g. *"Figure 1, illustration of three runs
> evaluated on two queries that yield the same MRR but with substantial differences
> in predictions"* (was "…is referenced here: For example, in Figure 1, …").
> **Known limitations (→ C3 / M2.5, not C2):** (a) a *table* caption still includes
> the table's row text (the caption line is merged with the grid in the text layer),
> so the spoken table caption is noisy — C3's grid summary replaces it with clean
> column/row prose; (b) a few **captionless** entries (sub-panels like `fig:3a` that
> inherit the parent's caption; and a "bare label on its own line" case, IEEE
> `TABLE I`) fall back to the reference sentence — M2.5 vision can fill these.
> **8 new hermetic tests in `tests/test_figure_table.py` + 4 in
> `tests/test_references.py` → 81 pass total.**_
>
> _**Decision resolved:** vision is optional (M2.5); the A2 caption+context base
> (option **b**) is shipped now — it's LLM-free, so the timing is testable without
> a model. `lfm3-vl:2b` is pulled and wired in M2.5 as the optional enhancement._

### M2.5 — Vision LLM through ollama python library  _status: [ ] not started_
- [ ] using `lfm3-vl:2b` as the default model add the optional vision description into the caption + context design whenever this or another config vision model is available on that machine. make sure
- [ ] make sure the vlm can also explain meaning from tables
- [ ] try and calculate vlm explanations in the background (when indexing) and serve them at the appropriate time
- [ ] use client and then chat so that a different ollama server can be configured by the user
- [x] update pyproject.toml without litellm and pdfplumber. also move piper-tts with the voice installation into the core dependencies. ollama should be in vision.
> 📝 comment: _**pyproject done (2026-11-25):** dropped `litellm`/`httpx`/`pdfplumber`;
> `piper-tts` moved into **core** deps; `ollama>=0.6` added to a new **`vision`** extra
> (the LLM client, M2.5, talks to a local Ollama server via the `ollama` library). The
> stale `litellm` comment in `config.py` and the `llm/__init__.py` docstring were also
> cleaned. 69 tests still pass; the full import graph loads._

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
- [ ] check documentation for a guided install for the core and optional systems
> 📝 comment:

### M7 — UI revamp  _status: [ ] not started_
- [ ] make sure the cue being read is the one in now reading at all times as it changes to the next cue to soon in previous versions
- [ ] make a light UI with high contrast as default but give a dark high contrast option. also add a dracula theme
- [ ] change to the now reading and transcript parts of the UI: text (particularly now reading) should have high contrast an larger for low-vision users (primary target of this tool). previous cues and forward cues should also show up similar the lyrics UI in Apple Music instead of the block system in place for previous milestones ![lyricsUI](https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTnxUzHYsARyk4-WAldA5Gc-DO6YlAn45W3kaIATVcuBryai1UxxX0iHHv_&s=10)
- [ ] transcript block can stay next to the paper when screen space is afforded
- [ ] show markdown version of table and the figure being explained whenever that's happening
> 📝 comment:

### M8 — Packaging (later, post-v1)  _status: [ ] not started_
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
│  ├─ orchestrator.py    [ ]   # scoped playback + transport
│  ├─ sections.py        [ ]   # heading detection (M5)
│  └─ scope.py           [ ]   # selection → timeline slice (M5)
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
- **Server hygiene** → `127.0.0.1` only, fixed default port 6767 (0 = random), local-only default (A13).

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
- [x] M1 complete — text reader: 2-col extract → reading order → Piper TTS → streamed WAV audio → pause/resume/stop/skip/replay (2026-11-24)
- [~] **M1.5 Checkpoint 1 (playback integrity) done — awaiting your listen** (2026-11-24). Fixed the **chunker sentence-drop** (~40% word loss on the Springer front-matter block, incl. the abstract opening+middle), the **pause/resume lookahead drop**, **cue-granular skip/replay** (now client-side, predictable), and a latent **TTS-failure stall**. 20 tests pass; live read-through confirms the abstract sentence is now spoken. **Checkpoint 2 (section-aware navigation + spoken section names) is planned and awaiting your go-ahead.**
- [ ] Ready to start **M2** (figures & tables + LLM explainer + first-reference `EXPLAIN` timeline)

> 📝 comment: _M1 DONE, awaiting user audio smoke test. espeak-ng→Piper pivot (see M1 note). A6 (live model/voice/style in UI) — voice+style selects done in M1; LLM model select is M4. A9 (media-style forward/rollback controls) done in M1. A10 (LLM section-detect fallback) deferred to M2/M4._
