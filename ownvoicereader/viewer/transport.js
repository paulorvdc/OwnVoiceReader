/* OwnVoiceReader viewer transport (M0).
 *
 * M0 responsibilities:
 *   - talk to the server's /api endpoints
 *   - render the loaded PDF with the vendored PDF.js
 *   - page navigation + zoom
 *   - drag-drop / file-open to load a PDF
 *
 * M1 will add the reading transport (WebSocket), an <audio> element, and the
 * play/pause/stop/skip/replay controls behind the disabled button group.
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
  };

  // --- state -------------------------------------------------------------
  let pdfDoc = null;       // pdf.js document
  let pageNum = 1;
  let scale = 1.1;
  let baseScale = 1.1;     // scale used before the user zoomed
  let renderTask = null;
  let renderVersion = 0;
  let currentDoc = null;   // the /api/info document object

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
        return await getJSON("/api/info");
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
  }

  function showReader() {
    el.landing.classList.add("hidden");
    el.reader.classList.remove("hidden");
    el.controls.classList.remove("hidden");
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
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
      // await in a version-safe way
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

  function fitWidth() {
    // Render a reference page to find the natural width, then scale to wrap.
    baseScale = scale;
    fitToContainer();
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
      const info = await getJSON("/api/load", { method: "POST", body: form });
      await onDocument(info);
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

  // --- controls ----------------------------------------------------------
  function wireControls() {
    $("prev-page").addEventListener("click", () => gotoPage(pageNum - 1));
    $("next-page").addEventListener("click", () => gotoPage(pageNum + 1));
    $("page-input").addEventListener("change", (e) => gotoPage(parseInt(e.target.value, 10) || 1));
    $("zoom-in").addEventListener("click", () => setScale(scale + 0.15));
    $("zoom-out").addEventListener("click", () => setScale(scale - 0.15));
    $("zoom-fit").addEventListener("click", fitWidth);

    // Keyboard: arrow keys navigate pages (A9: media-style controls come in M1).
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); gotoPage(pageNum - 1); }
      if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); gotoPage(pageNum + 1); }
      if (e.key === "+" || e.key === "=") { e.preventDefault(); setScale(scale + 0.15); }
      if (e.key === "-") { e.preventDefault(); setScale(scale - 0.15); }
      if (e.key === "f") { e.preventDefault(); fitWidth(); }
    });
  }

  // --- boot --------------------------------------------------------------
  async function onDocument(info) {
    currentDoc = info.document;
    renderDocInfo(info);
    if (info.loaded) {
      await loadPdf();
    } else {
      showLanding();
    }
  }

  async function boot() {
    setStatus("connecting…");
    wireDropZone();
    wireControls();
    try {
      const info = await fetchInfo();
      await onDocument(info);
      if (!info.loaded) setStatus("no document loaded — drop a PDF or run `ownvoicereader paper.pdf`");
    } catch (e) {
      setStatus("could not reach the server: " + e.message +
        " (is OwnVoiceReader running? the URL should open it.)", "err");
    }
  }

  window.addEventListener("load", boot);
  // Re-fit on window resize so a zoomed page doesn't overflow.
  window.addEventListener("resize", () => { /* no-op in M0; M1 keeps last scale */ });
})();
