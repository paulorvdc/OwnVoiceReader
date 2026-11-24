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
 *   - play / pause / stop / skip / replay controls
 *   - a "now reading" bar, a transcript, and a highlight overlay that follows
 *     the text being spoken (page + bbox).
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
    btnNext: $("btn-next"),
    btnReplay: $("btn-replay"),
    progress: $("progress"),
    styleSelect: $("style-select"),
    voiceSelect: $("voice-select"),
    nowReading: $("now-reading"),
    nowText: $("now-text"),
    transcript: $("transcript"),
    transcriptToggle: $("transcript-toggle"),
    overlay: $("highlight-overlay"),
    highlightBox: $("highlight-box"),
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

  // The page currently rendered, retained so the highlight overlay can map
  // PDF user-space bboxes to canvas pixels after a re-render.
  let lastViewport = null;

  async function renderPage() {
    if (!pdfDoc) return;
    const my = ++renderVersion;
    try {
      const page = await pdfDoc.getPage(pageNum);
      if (my !== renderVersion) return; // a newer render superseded this one

      const viewport = page.getViewport({ scale });
      lastViewport = viewport;
      const dpr = window.devicePixelRatio || 1;
      el.canvas.width = Math.floor(viewport.width * dpr);
      el.canvas.height = Math.floor(viewport.height * dpr);
      el.canvas.style.width = Math.floor(viewport.width) + "px";
      el.canvas.style.height = Math.floor(viewport.height) + "px";
      // size the overlay to match the canvas
      el.overlay.style.width = el.canvas.style.width;
      el.overlay.style.height = el.canvas.style.height;
      const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined;

      const task = page.render({ canvasContext: ctx, viewport, transform });
      await (task.promise ? task.promise : task);
      if (my !== renderVersion) return;

      el.pageInput.value = String(pageNum);
      el.zoomLabel.textContent = Math.round(scale * 100) + "%";
      // Redraw the highlight for the current cue (if it belongs to this page).
      if (reader && reader.currentBBox && reader.currentPage === pageNum - 1) {
        positionHighlight(reader.currentBBox);
      } else {
        el.overlay.style.display = "none";
      }
    } catch (e) {
      setStatus("render error: " + e.message, "err");
    }
  }

  function positionHighlight(bbox) {
    // bbox is PDF user space (origin top-left, points). The viewport maps
    // user space -> canvas pixels at the current scale.
    const vp = lastViewport || { transform: (p) => p };
    const tl = vp.transform([bbox.x0, bbox.y0]);
    const br = vp.transform([bbox.x1, bbox.y1]);
    const x = Math.max(0, Math.min(tl[0], br[0]));
    const y = Math.max(0, Math.min(tl[1], br[1]));
    const w = Math.abs(br[0] - tl[0]);
    const h = Math.abs(br[1] - tl[1]);
    el.highlightBox.style.left = x + "px";
    el.highlightBox.style.top = y + "px";
    el.highlightBox.style.width = w + "px";
    el.highlightBox.style.height = h + "px";
    el.overlay.style.display = "block";
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
      this.waitingForCue = false;
      this.expectingAudio = false;
      this.audioMeta = null;
      this.cueCount = 0;
      this.pos = 0;
      this.currentPage = null;
      this.currentBBox = null;
      this.voice = null;
      this.onStatus = null;
    }

    connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(proto + "//" + location.host + "/ws");
      this.ws.onmessage = (e) => this.onMessage(e);
      this.ws.onopen = () => {
        setStatus("connected — press ▶ to read the paper aloud", "ok");
      };
      this.ws.onclose = () => {
        setStatus("connection closed — reloading…", "");
        setTimeout(() => location.reload(), 800);
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
      this.updateVoiceUI();
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
      // Transcript + "now reading" + follow-along highlight.
      this.addTranscript(ev.text, ev.type === "notice" ? "notice" : "");
      el.nowText.textContent = ev.text || "…";
      if (ev.page != null) {
        this.currentPage = ev.page;
        this.currentBBox = ev.meta && ev.meta.bbox ? ev.meta.bbox : null;
        const targetPage = ev.page + 1; // PDF.js pages are 1-based
        if (targetPage !== pageNum) {
          gotoPage(targetPage);
        } else if (this.currentBBox) {
          positionHighlight(this.currentBBox);
        }
      }
    }

    onBinary(data) {
      if (!this.expectingAudio) return;
      this.expectingAudio = false;
      const blob = new Blob([data], { type: "audio/wav" });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
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
        this.current = this.queue.shift();
        this.current.onended = () => {
          this.current = null;
          this.pump();
          this.maybeFetchMore();
        };
        try { this.current.play(); } catch (_) { /* autoplay may be blocked */ }
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
      // React to explicit state changes coming from the server.
      if (s.state === "stopped") {
        this.playing = false;
        this.current = null;
        this.queue.length = 0;
        this.waitingForCue = false;
        el.overlay.style.display = "none";
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
      if (this.current && this.current.paused) {
        try { await this.current.play(); } catch (_) {}
      }
      // Tell the server to (re)start / resume producing from the current
      // position; this also syncs its state to "playing".
      this.send({ cmd: "resume" });
      this.pump();
      this.maybeFetchMore();
    }

    pause() {
      this.playing = false;
      if (this.current && !this.current.paused) this.current.pause();
      this.queue.length = 0; // drop the lookahead; resume refetches
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
      this.pos = 0;
      this.currentPage = null;
      this.currentBBox = null;
      el.overlay.style.display = "none";
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

    skip() {
      if (this.current) { this.current.pause(); this.current = null; }
      this.queue.length = 0;
      this.playing = true;
      el.btnPlay.textContent = "⏸";
      this.waitingForCue = true;
      this.send({ cmd: "skip" });
    }

    replay() {
      if (this.current) { this.current.pause(); this.current = null; }
      this.queue.length = 0;
      this.playing = true;      // play the re-emitted cue even if we were paused
      this.waitingForCue = true;
      el.btnPlay.textContent = "⏸";
      this.send({ cmd: "replay" });
    }

    // When a `continue`/`skip`/`start`/`replay` produced its cue (audio or end),
    // the flag is cleared in onBinary / onEnd.

    // --- UI wiring -----------------------------------------------------
    enableControls(enabled) {
      el.voiceControls.classList.toggle("disabled", !enabled);
      [el.btnPlay, el.btnStop, el.btnNext, el.btnReplay].forEach((b) => (b.disabled = !enabled));
    }

    updateVoiceUI() {
      const styles = (this.voice && this.voice.styles) || ["academic-calm", "quick-scan", "narrative", "lecture"];
      el.styleSelect.innerHTML = styles.map((s) => `<option value="${s}">${s}</option>`).join("");
      el.styleSelect.disabled = false;
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

    setProgress(pos, n) {
      el.progress.textContent = `${pos || 0}/${n || 0}`;
    }

    addTranscript(text, cls) {
      const line = document.createElement("div");
      line.className = "line" + (cls ? " " + cls : "");
      // mark the previous current line as no longer current
      const prev = el.transcript.querySelector(".line.current");
      if (prev) prev.classList.remove("current");
      line.textContent = text;
      if (cls !== "notice") line.classList.add("current");
      el.transcript.appendChild(line);
      // keep the transcript from growing unbounded
      while (el.transcript.children.length > 400) el.transcript.removeChild(el.transcript.firstChild);
      if (el.transcript.classList.contains("open")) {
        el.transcript.scrollTop = el.transcript.scrollHeight;
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
    el.overlay.style.display = "none";
    reader = new Reader();
    reader.connect();
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
    el.btnNext.addEventListener("click", () => { if (reader) reader.skip(); });
    el.btnReplay.addEventListener("click", () => { if (reader) reader.replay(); });
    el.transcriptToggle.addEventListener("click", () => {
      const open = el.transcript.classList.toggle("open");
      el.transcriptToggle.textContent = open ? "transcript ▴" : "transcript ▾";
    });

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
