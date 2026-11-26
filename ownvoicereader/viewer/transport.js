/* OwnVoiceReader viewer transport (M1).
 *
 * M0 responsibilities:
 *   - talk to the server's /api endpoints
 *   - render the loaded PDF with the vendored PDF.js
 *   - page navigation + zoom
 *   - drag-drop / file-open to load a PDF
 *
 * M1 adds the reading transport:
 *   - a WebSocket to /ws that streams reading events + WAV audio
 *   - an HTML5 <audio> element that plays the streamed audio (A9)
 *   - play / pause / stop + section & paragraph navigation controls
 *   - a "now reading" bar, a transcript, and auto page-scroll that follows the
 *     text being spoken (the cue's page). M5 C1 scrapped the PDF overlay
 *     highlight; C3/C4 will add a transcript-based follow-along instead.
 */
(function () {
  "use strict";

  // --- DOM refs ----------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const el = {
    landing: $("landing"),
    reader: $("reader"),
    controls: $("controls"),
    drop: $("drop"),
    openBtn: $("open-btn"),
    fileInput: $("file-input"),
    docInfo: $("doc-info"),
    status: $("status"),
    canvas: $("pdf-canvas"),
    pageInput: $("page-input"),
    pageCount: $("page-count"),
    zoomLabel: $("zoom-label"),
    // M1 UI
    voiceControls: $("voice-controls"),
    voiceSettings: $("voice-settings"),
    btnPlay: $("btn-play"),
    btnStop: $("btn-stop"),
    btnPrevCue: $("btn-prev-cue"),
    btnNextCue: $("btn-next-cue"),
    btnNext: $("btn-next"),
    btnReplay: $("btn-replay"),
    changePdf: $("change-pdf"),
    progress: $("progress"),
    styleSelect: $("style-select"),
    voiceSelect: $("voice-select"),
    // M4: live model picker + local-only toggle + "Save settings" button.
    modelSelect: $("model-select"),
    localOnlyChk: $("local-only-chk"),
    saveSettings: $("save-settings"),
    // M4: the Ollama host input + the "Refresh models" button (re-point + re-probe).
    ollamaHost: $("ollama-host"),
    refreshModels: $("refresh-models"),
    nowReading: $("now-reading"),
    nowText: $("now-text"),
    nowSection: $("now-section"),
    transcript: $("transcript"),
    transcriptToggle: $("transcript-toggle"),
    themeBtn: $("theme-btn"),
    audioOut: $("audio-out"),
  };

  // --- state -------------------------------------------------------------
  let pdfDoc = null;       // pdf.js document
  let pageNum = 1;
  let scale = 1.1;
  let baseScale = 1.1;     // scale used before the user zoomed
  let renderVersion = 0;
  let currentDoc = null;   // the /api/info document object
  let info = null;         // last /api/info payload
  // M4: the user may have typed a host into the input — once they do, a `hello`
  // pre-fill must NOT clobber it (e.g. after a "Change PDF" opens a new connection).
  let hostEdited = false;

  const ctx = el.canvas.getContext("2d");

  // --- helpers -----------------------------------------------------------
  function setStatus(msg, kind) {
    el.status.textContent = msg || "";
    el.status.className = "status" + (kind ? " " + kind : "");
  }

  async function getJSON(url, opts) {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new Error(detail || ("HTTP " + resp.status));
    }
    return resp.json();
  }

  // Retry a few times: the browser may open before the server is fully up.
  async function fetchInfo(retries = 6) {
    for (let i = 0; i < retries; i++) {
      try {
        const data = await getJSON("/api/info");
        return data;
      } catch (e) {
        if (i === retries - 1) throw e;
        setStatus("connecting to OwnVoiceReader server…", "");
        await new Promise((r) => setTimeout(r, 400));
      }
    }
  }

  function showLanding() {
    el.landing.classList.remove("hidden");
    el.reader.classList.add("hidden");
    el.controls.classList.add("hidden");
    el.nowReading.classList.add("hidden");
  }

  function showReader() {
    el.landing.classList.add("hidden");
    el.reader.classList.remove("hidden");
    el.controls.classList.remove("hidden");
    el.nowReading.classList.remove("hidden");
  }

  function renderDocInfo(info) {
    const doc = info.document || {};
    let html = "";
    if (doc.title) html += '<div class="title">' + escapeHtml(doc.title) + "</div>";
    html += '<div>' + (doc.page_count || "?") + " pages · " +
            (doc.block_count || 0) + " blocks";
    if (info.providers && info.providers.length) {
      info.providers.forEach((p) => {
        html += ' <span class="badge env" title="detected in your OS environment">' +
                escapeHtml(p) + "</span>";
      });
    }
    el.docInfo.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\u0022/g, "&quot;");
  }

  // --- PDF.js rendering --------------------------------------------------
  async function loadPdf() {
    if (typeof window.pdfjsLib === "undefined") {
      setStatus("PDF.js failed to load", "err");
      return;
    }
    setStatus("loading PDF…");
    try {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        "/viewer/vendor/pdfjs/pdf.worker.min.js";
      const resp = await fetch("/pdf", { headers: { Accept: "application/pdf" } });
      if (!resp.ok) throw new Error("no PDF served (HTTP " + resp.status + ")");
      const buf = await resp.arrayBuffer();
      pdfDoc = await window.pdfjsLib.getDocument({ data: new Uint8Array(buf) }).promise;

      pageNum = 1;
      el.pageCount.textContent = String(pdfDoc.numPages);
      el.pageInput.max = pdfDoc.numPages;
      el.pageInput.value = "1";

      showReader();
      await renderPage();
      setStatus("ready — " + pdfDoc.numPages + " pages", "ok");
    } catch (e) {
      setStatus("could not render PDF: " + e.message, "err");
    }
  }

  async function renderPage() {
    if (!pdfDoc) return;
    const my = ++renderVersion;
    try {
      const page = await pdfDoc.getPage(pageNum);
      if (my !== renderVersion) return; // a newer render superseded this one

      const viewport = page.getViewport({ scale });
      const dpr = window.devicePixelRatio || 1;
      el.canvas.width = Math.floor(viewport.width * dpr);
      el.canvas.height = Math.floor(viewport.height * dpr);
      el.canvas.style.width = Math.floor(viewport.width) + "px";
      el.canvas.style.height = Math.floor(viewport.height) + "px";
      const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined;

      const task = page.render({ canvasContext: ctx, viewport, transform });
      await (task.promise ? task.promise : task);
      if (my !== renderVersion) return;

      el.pageInput.value = String(pageNum);
      el.zoomLabel.textContent = Math.round(scale * 100) + "%";
    } catch (e) {
      setStatus("render error: " + e.message, "err");
    }
  }

  function gotoPage(n) {
    if (!pdfDoc) return;
    pageNum = Math.min(Math.max(1, n), pdfDoc.numPages);
    renderPage();
  }

  function setScale(s) {
    scale = Math.min(4, Math.max(0.3, s));
    renderPage();
  }

  function fitToContainer() {
    if (!pdfDoc) return;
    pdfDoc.getPage(pageNum).then((page) => {
      const base = page.getViewport({ scale: 1 });
      const wrap = document.getElementById("pdf-wrap");
      const avail = wrap.clientWidth - 32; // padding
      scale = Math.max(0.3, Math.min(4, avail / base.width));
      renderPage();
    });
  }

  // --- drop zone / file open --------------------------------------------
  async function uploadFile(file) {
    if (!file) return;
    setStatus("uploading " + file.name + "…");
    const form = new FormData();
    form.append("file", file);
    try {
      const data = await getJSON("/api/load", { method: "POST", body: form });
      await onDocument(data);
    } catch (e) {
      setStatus("upload failed: " + e.message, "err");
    }
  }

  function wireDropZone() {
    el.openBtn.addEventListener("click", () => el.fileInput.click());
    el.fileInput.addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) uploadFile(f);
      e.target.value = ""; // allow re-selecting the same file
    });

    ["dragenter", "dragover"].forEach((ev) =>
      el.drop.addEventListener(ev, (e) => {
        e.preventDefault();
        el.drop.classList.add("dragover");
      }));
    ["dragleave", "drop"].forEach((ev) =>
      el.drop.addEventListener(ev, (e) => {
        e.preventDefault();
        el.drop.classList.remove("dragover");
      }));
    el.drop.addEventListener("drop", (e) => {
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadFile(f);
    });
  }

  // =====================================================================
  // M1: the reading transport (WebSocket + audio + controls)
  // =====================================================================
  let reader = null;

  class Reader {
    constructor() {
      this.ws = null;
      this.state = "idle";       // idle | playing | paused | stopped | done
      this.playing = false;
      this.queue = [];           // pending Audio objects (lookahead buffer)
      this.current = null;       // the Audio currently playing
      // M5 C1: each queued Audio carries the event + transcript line that
      // produced it, so "now reading" can be keyed to *playback start* rather
      // than to when the event arrived — the lookahead can make the server
      // *produce* the next cue a beat before it is actually heard.
      this._pendingAudioEvent = null;
      this._pendingLine = null;
      this.waitingForCue = false;
      this.expectingAudio = false;
      this.audioMeta = null;
      this._jumping = false;   // a section jump is in flight (hold stale cues)
      this.cueCount = 0;
      this.pos = 0;
      this.voice = null;
      this.vision = null;
      // M4: live settings (mirrored onto the UI; sent back on a `state` frame).
      this.model = null;
      this.style = null;
      this.localOnly = true;
      this.onStatus = null;
      this.reconnectAttempts = 0;
      this._reconnectGaveUp = false;
    }

    connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = proto + "://" + location.host + "/ws";
      this.ws = new WebSocket(wsUrl);
      this.ws.onmessage = (e) => this.onMessage(e);
      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this._reconnectGaveUp = false;
        setStatus("connected — press ▶ to read the paper aloud", "ok");
      };
      // A closed socket is common (a brief blip, or the user reloading the
      // page after a server restart). Retry in place a few times with
      // backoff instead of nuking the whole page — that previously produced an
      // infinite reload loop. We give up after a handful of tries so a truly
      // dead server surfaces an error the user can act on.
      this.ws.onclose = () => {
        if (this._reconnectGaveUp) return;
        this.reconnectAttempts = (this.reconnectAttempts || 0) + 1;
        if (this.reconnectAttempts >= 5) {
          this._reconnectGaveUp = true;
          setStatus("connection lost — press ▶ or reload to retry", "err");
          return;
        }
        const delay = 500 * this.reconnectAttempts;
        setStatus(`connection closed — retrying in ${delay / 1000}s…`, "");
        setTimeout(() => this.connect(), delay);
      };
      this.ws.onerror = () => setStatus("connection error", "err");
    }

    send(obj) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(obj));
      }
    }

    onMessage(e) {
      if (typeof e.data !== "string") {
        this.onBinary(e.data);
        return;
      }
      let msg;
      try { msg = JSON.parse(e.data); } catch (_) { return; }
      switch (msg.type) {
        case "hello":
          this.onHello(msg);
          break;
        case "event":
          this.onEvent(msg.event);
          break;
        case "audio":
          this.expectingAudio = true;
          this.audioMeta = msg;
          break;
        case "end":
          this.onEnd();
          break;
        case "state":
          this.onState(msg);
          break;
        case "vision":
          // M4: a `set_ollama_host` reply — the fresh probe repopulates the picker
          // + badge and reflects the host the reader now points at.
          this.onVisionRefresh(msg);
          break;
        case "notice":
          this.addTranscript(msg.text, "notice");
          setStatus(msg.text, "ok");
          break;
        case "error":
          setStatus("error: " + msg.text, "err");
          break;
      }
    }

    onHello(msg) {
      this.cueCount = msg.cue_count || 0;
      this.voice = msg.voice || { available: false };
      this.vision = msg.vision || { available: false, installed: false, model: null, models: [] };
      // M4: the current live settings come from the orchestrator's status frame.
      const st = msg.state || {};
      this.style = st.style || "academic-calm";
      this.model = st.llm_model || this.vision.model || null;
      this.localOnly = st.local_only !== undefined ? st.local_only : true;
      if (el.localOnlyChk) el.localOnlyChk.checked = this.localOnly;
      this.updateVoiceUI();
      this.updateModelUI();
      this.updateVisionUI();
      if (el.saveSettings) el.saveSettings.disabled = false;
      // M4: pre-fill the Ollama host from where the client actually points (the
      // vision info carries the effective host); enable the input + "Refresh".
      // Don't clobber a host the user has typed — a "Change PDF" reconnection would
      // otherwise wipe it.
      if (el.ollamaHost) {
        el.ollamaHost.disabled = false;
        if (!hostEdited) {
          el.ollamaHost.value = this.vision.host || el.ollamaHost.placeholder;
        }
      }
      if (el.refreshModels) el.refreshModels.disabled = false;
      if (this.cueCount === 0) {
        this.enableControls(false);
        setStatus("no readable text found in this PDF", "err");
      } else {
        this.enableControls(true);
        setStatus(`${this.cueCount} segments ready · voice: ${
          this.voice.available ? this.voice.engine : "unavailable"}`,
          this.voice.available ? "ok" : "");
      }
    }

    onEvent(ev) {
      // Hold stale cues produced by a `continue` that was in flight *before* a
      // section jump — the jump's `state` frame resyncs the counter + section.
      if (this._jumping) return;
      // Append the cue's transcript line now, but DO NOT mark it "current" or move
      // "now reading" yet. With the lookahead buffer the server can *produce* the
      // next cue a beat before it is actually heard, so keying "now reading" off the
      // event is what made it jump to the next cue too soon (M5 C1). Instead the line
      // becomes "current" — and "now reading" (text / section chip / counter /
      // PDF page) follows it — when its audio *starts playing* (pump), or
      // immediately for cues that produce no audio (a notice, or voice-off mode).
      const line = this.addTranscript(ev.text, this._lineClass(ev));
      const hasAudio = ev.type !== "notice" && this.voice && this.voice.available;
      if (hasAudio) {
        // Stash the event + its transcript line for the audio that's coming; pump()
        // keys "now reading" to it when that audio actually starts to play.
        this._pendingAudioEvent = ev;
        this._pendingLine = line;
      } else if (ev.type === "notice") {
        // A TTS failure: the cue produced no sound, so keep "now reading" on the
        // last *heard* cue — but its position did advance, so update the counter,
        // section chip, and PDF page (not the now-reading text), then pull the next
        // cue so the stream doesn't stall on a cue that produced no sound.
        this._setNowReading(ev, null, false);
        this.waitingForCue = false;
        this.maybeFetchMore();
      } else {
        // No TTS (voice-off mode): nothing will be *heard*, so show this cue as
        // "now reading" the moment it arrives, the way the reader advanced before.
        this._setNowReading(ev, line);
      }
    }

    // A transcript line class (M2.5). Explanation units are tagged with
    // `meta.phase` ("start"/"body"/"end"); we style them distinctly so the
    // listener can see where the reader diverts to explain a figure/table and
    // when it returns. Markers ("Now, a closer look…" / "Back to the text.") are
    // bolder than the body they bracket.
    _lineClass(ev) {
      if (ev.type === "notice") return "notice";
      const phase = ev.meta && ev.meta.phase;
      if (phase === "start" || phase === "end") return "explain-marker";
      if (phase === "body") return "explain";
      return "";
    }

    onBinary(data) {
      if (this._jumping || !this.expectingAudio) return;
      this.expectingAudio = false;
      const blob = new Blob([data], { type: "audio/wav" });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      // M5 C1: bind this Audio to the event + transcript line that produced it, so
      // pump() can key "now reading" to *this* audio starting to play.
      audio._event = this._pendingAudioEvent;
      audio._line = this._pendingLine;
      this._pendingAudioEvent = null;
      this._pendingLine = null;
      this.queue.push(audio);
      // A cue fully arrived: we may fetch the next one (lookahead).
      this.waitingForCue = false;
      this.pump();
      this.maybeFetchMore();
    }

    // Play from the queue; when it empties, fetch the next cue (lookahead).
    pump() {
      if (!this.playing) return;
      if (this.current) return;
      if (this.queue.length) {
        const cue = this.queue.shift();
        // M5 C1: wire the *critical* audio path FIRST — the onended callback that
        // advances to the next cue, then play() — so a failure in the purely
        // cosmetic "now reading" update below can never stall the reader. That
        // coupling was the C1 regression: an update that threw or hung left
        // this.current set-but-unplayed, so onended never fired, the next cue
        // never came up, and cues were dropped.
        cue.onended = () => {
          this.current = null;
          this.pump();
          this.maybeFetchMore();
        };
        this.current = cue;
        try { cue.play(); } catch (_) { /* autoplay may be blocked */ }
        // M5 C1: key "now reading" (text / section chip / counter / PDF page /
        // transcript current line) to the cue that is now being heard.
        // It is guarded because it is cosmetic and must not be able to break the
        // audio pump — a throw here must not drop the next cue.
        try {
          this._setNowReading(cue._event, cue._line);
        } catch (err) {
          console.warn("OwnVoiceReader: 'now reading' update failed", err);
        }
        return;
      }
      this.maybeFetchMore();
    }

    maybeFetchMore() {
      if (!this.playing) return;
      if (this.queue.length >= 1) return;      // already have the next one
      if (this.waitingForCue) return;
      this.waitingForCue = true;
      this.send({ cmd: "continue" });
    }

    onEnd() {
      this.playing = false;
      this.state = "done";
      this.waitingForCue = false;
      this.enableControls(true); // allow replay/restart
      el.btnPlay.textContent = "▶";
      this.setProgress(this.cueCount, this.cueCount);
      setStatus("finished reading", "ok");
      el.nowText.textContent = "— end of reading —";
    }

    onState(s) {
      if (s.pos != null) this.pos = s.pos;
      if (s.n != null) this.cueCount = s.n;
      if (s.state) this.state = s.state;
      this.setProgress(s.pos, s.n);
      if (s.voice) { this.voice.voice = s.voice; }
      if (s.style && el.styleSelect && el.styleSelect.value !== s.style) {
        el.styleSelect.value = s.style;
      }
      // M4: a `set_model` / `set_local_only` reply carries the new value back so
      // the picker + toggle resync (and so another tab's choice is reflected).
      if (s.llm_model != null && el.modelSelect) {
        this.model = s.llm_model;
        if (el.modelSelect.value !== s.llm_model) el.modelSelect.value = s.llm_model;
      }
      if (s.local_only != null && el.localOnlyChk) {
        this.localOnly = s.local_only;
        el.localOnlyChk.checked = s.local_only;
      }
      if (s.section) this.updateSection(s.section);
      // A `next_section`/`prev_section` reply: the server moved the playhead and
      // this `state` frame carries the new section + position. Resync the UI,
      // then resume pulling cues from the new position (the section-start cue
      // speaks its lead-in). Any stale cue still in flight is ignored because we
      // set `_jumping` when the jump started.
      if (this._jumping) {
        this._jumping = false;
        this.updateSection(s.section);
        this.setProgress(s.pos, s.n);
        if (s.n && s.pos >= s.n) {
          this.onEnd();
        } else {
          this.playing = true;
          el.btnPlay.textContent = "⏸";
          this.send({ cmd: "resume" });
          this.pump();
          this.maybeFetchMore();
        }
        return;
      }
      // React to explicit state changes coming from the server.
      if (s.state === "stopped") {
        this.playing = false;
        this.current = null;
        this.queue.length = 0;
        this.waitingForCue = false;
        el.btnPlay.textContent = "▶";
        el.nowText.textContent = "stopped — ready to play from the start";
        this.setProgress(0, this.cueCount);
      } else if (s.state === "paused") {
        this.playing = false;
        el.btnPlay.textContent = "▶";
      } else if (s.state === "playing") {
        el.btnPlay.textContent = "⏸";
      }
    }

    // --- controls ------------------------------------------------------
    async play() {
      if (this.state === "done") { this.restart(); return; }
      this.playing = true;
      el.btnPlay.textContent = "⏸";
      // `resume` is state-only on the server; we pull each cue with `continue`
      // (via maybeFetchMore), so a resume never produces a cue we already hold.
      this.send({ cmd: "resume" });
      if (this.current && this.current.paused) {
        try { await this.current.play(); } catch (_) {}
      }
      this.pump();
      this.maybeFetchMore();
    }

    pause() {
      this.playing = false;
      if (this.current && !this.current.paused) this.current.pause();
      // KEEP the lookahead: the server's playhead already advanced past it, so
      // discarding it here would skip that cue when we resume. We resume it from
      // the queue instead.
      this.send({ cmd: "pause" });
      el.btnPlay.textContent = "▶";
      setStatus("paused", "");
    }

    togglePlay() {
      if (this.playing) this.pause(); else this.play();
    }

    stop() {
      this.playing = false;
      if (this.current) { this.current.pause(); this.current = null; }
      this.queue.length = 0;
      this.waitingForCue = false;
      this._pendingAudioEvent = null;
      this._pendingLine = null;
      this.pos = 0;
      el.btnPlay.textContent = "▶";
      this.send({ cmd: "stop" });
      this.setProgress(0, this.cueCount);
      setStatus("stopped — at the start", "");
    }

    restart() {
      this.stop();
      // Small delay so the server resets before we resume.
      setTimeout(() => this.play(), 150);
    }

    // `waitingForCue` is set when a `continue` goes out and cleared when its
    // audio (or end) arrives, in onBinary / onEnd.

    // --- navigation jumps (M1.5 checkpoint 2 + fine-cue jump/rollback) -----
    // Two granularities share one server-side mechanism (`_jump` + a `state`
    // reply): the coarse ⏭ / ⏮ snap to section starts (the user thinks in
    // sections, not ~200-char cues), and the fine ⏩ / ⏪ move exactly one cue
    // (a ~paragraph). Every jump moves the server's playhead — the single
    // source of truth — and the reply's `state` frame resyncs the counter +
    // "now reading" chip (see onState's `_jumping` branch). A *double-click* on
    // ⏮ skips a *whole* section back (`prevSectionSkip` → the section *before* the
    // current one); a single click just re-hears the current section.
    _jump(cmd, statusText) {
      // Stop whatever is playing and clear the stale lookahead queue so a cue
      // produced *before* the jump can't be re-heard. The server moves the
      // playhead and replies with a `state` frame; we hold until it arrives.
      if (this.current) { this.current.pause(); this.current = null; }
      this.queue.length = 0;
      this.waitingForCue = false;
      this.expectingAudio = false;
      this._pendingAudioEvent = null;
      this._pendingLine = null;
      this._jumping = true;
      el.btnPlay.textContent = "⏸";
      setStatus(statusText, "");
      this.send({ cmd });
    }

    nextSection() { this._jump("next_section", "jumping to next section…"); }
    prevSection() { this._jump("prev_section", "rolling back to section…"); }
    // Double-click on ⏮: skip back a *whole* section (the section *before* the
    // current one), not a re-hear of the current section's start.
    prevSectionSkip() { this._jump("prev_section_skip", "skipping back a section…"); }
    nextCue() { this._jump("next_cue", "skip to next paragraph…"); }
    prevCue() { this._jump("prev_cue", "back to previous paragraph…"); }

    // Update the "now reading" section chip from a section object
    // ({index, label, kind}). Front-matter kinds (title/abstract/…) show their
    // label as-is; numbered sections get a "§ " prefix.
    updateSection(sec) {
      if (!el.nowSection) return;
      if (!sec || sec.index == null || sec.index < 0) {
        el.nowSection.textContent = "";
        return;
      }
      const kind = sec.kind;
      const prefix = (kind === "section" || kind === "subsection") ? "§ " : "";
      el.nowSection.textContent = prefix + (sec.label || "");
    }

    // --- UI wiring -----------------------------------------------------
    enableControls(enabled) {
      el.voiceControls.classList.toggle("disabled", !enabled);
      [el.btnPlay, el.btnStop, el.btnNext, el.btnReplay,
       el.btnNextCue, el.btnPrevCue].forEach((b) => (b.disabled = !enabled));
    }

    updateVoiceUI() {
      const styles = (this.voice && this.voice.styles) || ["academic-calm", "quick-scan", "narrative", "lecture"];
      el.styleSelect.innerHTML = styles.map((s) => `<option value="${s}">${s}</option>`).join("");
      el.styleSelect.disabled = false;
      if (this.style) el.styleSelect.value = this.style;
      el.styleSelect.onchange = () => this.send({ cmd: "set_style", style: el.styleSelect.value });
      const voices = (this.voice && this.voice.voices) || [];
      if (voices.length) {
        el.voiceSelect.innerHTML = voices.map((v) => `<option value="${v}">${v}</option>`).join("");
        if (this.voice.default_voice) el.voiceSelect.value = this.voice.default_voice;
        el.voiceSelect.disabled = false;
        el.voiceSelect.onchange = () => this.send({ cmd: "set_voice", voice: el.voiceSelect.value });
      } else {
        el.voiceSelect.innerHTML = '<option value="">(local voice)</option>';
        el.voiceSelect.disabled = true;
      }
      // Reflect voice availability in the badge.
      if (!this.voice || !this.voice.available) {
        el.docInfo.insertAdjacentHTML(
          "beforeend",
          ' <span class="badge off" title="No local TTS engine — text only">voice: off</span>');
      } else {
        el.docInfo.insertAdjacentHTML(
          "beforeend",
          ` <span class="badge voice" title="Local voice: ${this.voice.engine}">voice: ${this.voice.engine}</span>`);
      }
    }

    // A read-only status badge for the vision LLM (M2.5): green + the model in
    // use when it is reachable, red ("off") when it is not — so the user knows to
    // start Ollama / pull the model. The badge shows *status*; the model *picker*
    // in the settings group (updateModelUI) is the M4 control for switching it.
    // Idempotent: clear any prior vision badge first so a reconnect does not
    // stack duplicates.
    updateVisionUI() {
      el.docInfo.querySelectorAll(".badge.vision, .badge.vision-off")
        .forEach((n) => n.remove());
      const v = this.vision || { available: false, installed: false, model: null };
      const model = v.model || "vision";
      if (v.available) {
        el.docInfo.insertAdjacentHTML(
          "beforeend",
          ` <span class="badge vision" title="Vision LLM ready: ${model} (local Ollama)">vision: ${model}</span>`);
      } else if (v.installed) {
        // Extra is present but the server/model is not reachable -> "set it up".
        el.docInfo.insertAdjacentHTML(
          "beforeend",
          ` <span class="badge vision-off" title="Vision off — start Ollama and pull ${model} (set OLLAMA_HOST to point at another server)">vision: ${model} · off</span>`);
      } else {
        // The [vision] extra is not installed.
        el.docInfo.insertAdjacentHTML(
          "beforeend",
          ' <span class="badge vision-off" title="Install the vision extra:  pip install &quot;ownvoicereader[vision]&quot;">vision: off</span>');
      }
    }

    // M4: the model picker. Options are the model ids the local Ollama server
    // reports (`vision.models`, cache-only from the last probe); the current model
    // is always present so a value outside the server's list is still selectable.
    // Switching the model is live (a `state` frame resyncs it) and persists on a
    // "Save" click. When the [vision] extra is absent there are no models, so the
    // picker just shows the configured value (setting it has no live effect until
    // vision is installed, but it is saved for next time).
    updateModelUI() {
      const v = this.vision || { installed: false, model: null, models: [] };
      const models = Array.from(new Set(
        (v.models || []).concat(v.model ? [v.model] : []).filter(Boolean)
      ));
      if (models.length) {
        el.modelSelect.innerHTML = models.map((m) =>
          `<option value="${escapeHtml(m)}">${m}</option>`).join("");
        el.modelSelect.disabled = false;
        if (this.model) el.modelSelect.value = this.model;
      } else {
        el.modelSelect.innerHTML =
          `<option value="${escapeHtml(v.model || "")}">${v.model || "(no vision model)"}</option>`;
        el.modelSelect.disabled = false;
      }
      el.modelSelect.title = v.installed
        ? "Vision / LLM model (local Ollama)"
        : "Vision extra not installed — set your preferred model for next time";
      el.modelSelect.onchange = () => this.send({ cmd: "set_model", llm_model: el.modelSelect.value });
    }

    // M4: the "Refresh models" button. Points the vision client at the host typed in
    // the input and asks the server to re-probe; the `vision` reply (onVisionRefresh)
    // repopulates the picker + badge — no page reload. An empty input means "default
    // Ollama host" (the OLLAMA_HOST env var or http://localhost:11434).
    refreshModels() {
      const host = el.ollamaHost ? el.ollamaHost.value.trim() : null;
      this.send({ cmd: "set_ollama_host", host: host || null });
      setStatus("pointing vision at " + (host || "default Ollama host") +
        " — refreshing models…", "");
    }

    // M4: a `set_ollama_host` reply. Repopulate the picker + badge from the fresh
    // probe and reflect the host the reader is now pointing at in the input.
    onVisionRefresh(msg) {
      this.vision = msg.vision || this.vision;
      if (el.ollamaHost) {
        el.ollamaHost.value = msg.host || this.vision.host || el.ollamaHost.value;
      }
      this.updateVisionUI();
      this.updateModelUI();
      const v = this.vision || {};
      if (v.available) {
        setStatus("vision ready: " + (v.model || "?") + " at " + (v.host || "?") +
          " · " + (v.models || []).length + " model(s)", "ok");
      } else if (v.installed) {
        setStatus("no reachable vision model at " + (v.host || "?") +
          " — start Ollama / pull a model", "");
      } else {
        setStatus("vision extra not installed — set a host for next time", "");
      }
    }

    // M4: the "Save" button. Persists the *current* UI settings to config.yaml via
    // POST /api/save (a durable, deliberate action — settings are NOT auto-saved on
    // every change). Empty picker values (e.g. the voice-off placeholder) send null
    // so the server leaves them at their current value rather than blanking them.
    // The Ollama host is persisted too (a non-secret endpoint the user can point at).
    async saveSettings() {
      const body = {
        voice: el.voiceSelect.value || null,
        style: el.styleSelect.value || null,
        llm_model: el.modelSelect.value || null,
        ollama_host: el.ollamaHost ? el.ollamaHost.value.trim() || null : null,
        local_only: el.localOnlyChk ? el.localOnlyChk.checked : null,
      };
      try {
        const data = await getJSON("/api/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setStatus("settings saved to " + (data.path || "config"), "ok");
      } catch (e) {
        setStatus("could not save settings: " + e.message, "err");
      }
    }

    setProgress(pos, n) {
      el.progress.textContent = `${pos || 0}/${n || 0}`;
    }

    // Append a transcript line. M5 C1: it no longer marks itself "current" or
    // auto-scrolls — that is deferred to when the cue is actually *heard*
    // (_setNowReading / _markCurrentLine), so the lookahead can't pull the
    // "current" marker ahead of the audio. Returns the created element so the
    // caller can bind it to the cue's audio.
    addTranscript(text, cls) {
      const line = document.createElement("div");
      line.className = "line" + (cls ? " " + cls : "");
      line.textContent = text;
      el.transcript.appendChild(line);
      // keep the transcript from growing unbounded
      while (el.transcript.children.length > 400) el.transcript.removeChild(el.transcript.firstChild);
      return line;
    }

    // M5 C1: the single place "now reading" is driven — the now-reading text, the
    // section chip, the cue counter, the auto page-scroll, and the transcript's
    // current line, all keyed to the cue that is being *heard*. Called at playback
    // start (pump) for cues with audio, and at event arrival for cues that produce
    // no sound. `updateText` is false for a notice (keep "now reading" on the last
    // *spoken* cue while its position still advances).
    _setNowReading(ev, lineEl, updateText = true) {
      if (!ev) return;
      if (updateText) el.nowText.textContent = ev.text || "…";
      if (ev.meta) {
        // Live cue counter + "now reading" section chip (M1.5 checkpoint 2): the
        // counter advances as each cue is *spoken*, not only on a pause.
        if (ev.meta.index != null) this.setProgress(ev.meta.index + 1, this.cueCount);
        if (ev.meta.section) this.updateSection(ev.meta.section);
      }
      if (ev.page != null) {
        // Auto-scroll the PDF to the cue's page so the reader stays on the page
        // being heard. (The PDF overlay highlight was scrapped in M5 C1 — C3/C4
        // add a transcript-based follow-along instead; this keeps the page jump
        // that predates it, which the user wants retained.)
        const targetPage = ev.page + 1; // PDF.js pages are 1-based
        if (targetPage !== pageNum) gotoPage(targetPage);
      }
      if (lineEl) this._markCurrentLine(lineEl);
    }

    _markCurrentLine(lineEl) {
      const prev = el.transcript.querySelector(".line.current");
      if (prev && prev !== lineEl) prev.classList.remove("current");
      lineEl.classList.add("current");
      // Keep the line being heard in view (scrolls the transcript only when the
      // current line is off-screen; a no-op while the transcript is closed).
      if (el.transcript.classList.contains("open")) {
        lineEl.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function setupReader() {
    // Tear down any previous reader (a new upload) to avoid leaking sockets
    // and stacking listeners.
    if (reader) {
      try {
        reader.ws.onclose = null;   // don't trigger the auto-reload
        reader.ws.onmessage = null;
        if (reader.ws) reader.ws.close();
      } catch (_) {}
      reader = null;
    }
    // Reset the transcript + "now reading" for the new document.
    el.transcript.innerHTML = "";
    el.nowText.textContent = "…";
    if (el.nowSection) el.nowSection.textContent = "";
    reader = new Reader();
    reader.connect();
  }

  // --- theme (M5 C2) -----------------------------------------------------
  // One header button cycles the colour scheme: light (high-contrast — the
  // default, the low-vision primary target) → dark (high-contrast) → Dracula.
  // The active scheme lives on <html data-theme> (the CSS variable blocks in
  // index.html key off it) and is persisted to localStorage under THEME_KEY so it
  // survives a reload. A tiny inline script in <head> applies the saved value
  // before first paint (so a saved Dracula doesn't flash as light); here we sync
  // the button icon/label to whatever is already on <html> and wire the cycle.
  const THEMES = [
    { name: "light",   icon: "🌞", label: "Light (high-contrast)" },
    { name: "dark",    icon: "🌙", label: "Dark (high-contrast)" },
    { name: "dracula", icon: "🧛", label: "Dracula" },
  ];
  const THEME_KEY = "ownvoicereader.theme";

  function applyTheme(name, persist) {
    const t = THEMES.find((x) => x.name === name) || THEMES[0];
    document.documentElement.setAttribute("data-theme", t.name);
    if (el.themeBtn) {
      el.themeBtn.textContent = t.icon;
      el.themeBtn.title = "Theme: " + t.label + " — click to cycle";
      el.themeBtn.setAttribute("aria-label", "Cycle theme; current: " + t.label);
    }
    if (persist) {
      try { localStorage.setItem(THEME_KEY, t.name); } catch (_) { /* private mode */ }
    }
  }

  function cycleTheme() {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const i = THEMES.findIndex((x) => x.name === cur);
    applyTheme(THEMES[(i + 1) % THEMES.length].name, true);
  }

  // --- controls ----------------------------------------------------------
  function wireControls() {
    $("prev-page").addEventListener("click", () => gotoPage(pageNum - 1));
    $("next-page").addEventListener("click", () => gotoPage(pageNum + 1));
    $("page-input").addEventListener("change", (e) => gotoPage(parseInt(e.target.value, 10) || 1));
    $("zoom-in").addEventListener("click", () => setScale(scale + 0.15));
    $("zoom-out").addEventListener("click", () => setScale(scale - 0.15));
    $("zoom-fit").addEventListener("click", fitToContainer);

    // Voice controls (wired once; they reference the current module-level `reader`).
    el.btnPlay.addEventListener("click", () => { if (reader) reader.togglePlay(); });
    el.btnStop.addEventListener("click", () => { if (reader) reader.stop(); });
    // Coarse section jumps: ⏭ snaps to the *next* section; ⏮ single-click rolls
    // back to the current/previous section's start (re-hear it), but a *double-click*
    // on ⏮ skips a *whole* section — to the start of the section *before* the
    // current one (a real "skip a chapter", not a re-hear). A short timer detects
    // the double so the two `click` events that precede a native `dblclick` don't
    // each fire a section jump.
    el.btnNext.addEventListener("click", () => { if (reader) reader.nextSection(); });
    let prevSectionClickTimer = null;
    el.btnReplay.addEventListener("click", () => {
      if (!reader) return;
      if (prevSectionClickTimer !== null) {
        clearTimeout(prevSectionClickTimer);
        prevSectionClickTimer = null;
        reader.prevSectionSkip();            // 2nd click in window → skip a whole section
      } else {
        prevSectionClickTimer = setTimeout(() => {
          prevSectionClickTimer = null;
          reader.prevSection();              // single click → back to current/prev section
        }, 250);
      }
    });
    // Fine jumps (M1.5 C3): ⏩ / ⏪ move exactly one cue (~paragraph). ⏪ always
    // rolls back one cue rather than to the current section's start.
    el.btnNextCue.addEventListener("click", () => { if (reader) reader.nextCue(); });
    el.btnPrevCue.addEventListener("click", () => { if (reader) reader.prevCue(); });
    // M4: the "Save settings" button persists the current pickers to config.yaml.
    el.saveSettings.addEventListener("click", () => { if (reader) reader.saveSettings(); });
    // M4: the "Refresh models" button re-points the vision client at the host typed in
    // the input and re-probes it (a `vision` reply repopulates the picker + badge).
    el.refreshModels.addEventListener("click", () => { if (reader) reader.refreshModels(); });
    // M4: the host input is typeable from the start (not gated on a document load);
    // once the user types, flag it so a later `hello` pre-fill won't clobber it.
    if (el.ollamaHost) {
      el.ollamaHost.disabled = false;
      el.ollamaHost.addEventListener("input", () => { hostEdited = true; });
    }
    // The local-only toggle is live (a `set_local_only` frame resyncs it); the
    // model + voice + style pickers wire their `change` handlers in updateModelUI /
    // updateVoiceUI so they reference the current reader.
    el.localOnlyChk.addEventListener("change", () => {
      if (reader) reader.send({ cmd: "set_local_only", local_only: el.localOnlyChk.checked });
    });
    // "Change PDF" loads a different document on the fly (no server restart):
    // it reuses the landing's hidden file input + the uploadFile → onDocument
    // flow, which tears down the old reader/WS and loads the new document.
    el.changePdf.addEventListener("click", () => el.fileInput.click());
    el.transcriptToggle.addEventListener("click", () => {
      const open = el.transcript.classList.toggle("open");
      el.transcriptToggle.textContent = open ? "transcript ▴" : "transcript ▾";
    });
    // M5 C2: sync the button to whatever scheme the inline head script already
    // applied to <html>, then cycle light → dark → Dracula on click (persisted).
    if (el.themeBtn) {
      applyTheme(document.documentElement.getAttribute("data-theme") || "light", false);
      el.themeBtn.addEventListener("click", cycleTheme);
    }

    // Keyboard: space toggles play/pause; arrows navigate pages; +/- zoom; f fit.
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (e.code === "Space" && reader) { e.preventDefault(); reader.togglePlay(); return; }
      if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); gotoPage(pageNum - 1); }
      if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); gotoPage(pageNum + 1); }
      if (e.key === "+" || e.key === "=") { e.preventDefault(); setScale(scale + 0.15); }
      if (e.key === "-") { e.preventDefault(); setScale(scale - 0.15); }
      if (e.key === "f") { e.preventDefault(); fitToContainer(); }
    });
  }

  // --- boot --------------------------------------------------------------
  async function onDocument(data) {
    info = data;
    currentDoc = data.document;
    renderDocInfo(data);
    if (data.loaded) {
      await loadPdf();
      setupReader();
    } else {
      showLanding();
    }
  }

  async function boot() {
    setStatus("connecting…");
    wireDropZone();
    wireControls();
    try {
      const data = await fetchInfo();
      await onDocument(data);
      if (!data.loaded) {
        setStatus("no document loaded — drop a PDF or run `ownvoicereader paper.pdf`");
      }
    } catch (e) {
      setStatus("could not reach the server: " + e.message +
        " (is OwnVoiceReader running? the URL should open it.)", "err");
    }
  }

  window.addEventListener("load", boot);
  // Re-fit on window resize so a zoomed page doesn't overflow.
  window.addEventListener("resize", () => { if (pdfDoc) renderPage(); });
})();
