/* pdfocr web UI: upload a PDF, follow the SSE progress stream, grab the text. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const drop = $("drop");
  const fileInput = $("file");
  const jobPanel = $("job");
  const pagesEl = $("pages");
  const actions = $("actions");
  const output = $("output");

  let currentId = null;
  let source = null;

  const fmtEta = (s) => (s == null ? "" : s < 60 ? ` · ~${Math.round(s)}s left` : ` · ~${Math.round(s / 60)}m left`);

  async function loadEngines() {
    try {
      const health = await fetch("/api/health").then((r) => r.json());
      $("engines").innerHTML = health.engines
        .map((e) => `<span class="pill ${e.available ? "on" : "off"}" title="${e.detail || ""}">${e.available ? "●" : "○"} ${e.name}</span>`)
        .join("");
    } catch {
      $("engines").innerHTML = '<span class="pill off">API unreachable</span>';
    }
  }

  function renderPages(pages) {
    if (pagesEl.childElementCount !== pages.length) {
      pagesEl.innerHTML = pages.map(() => '<div class="page"></div>').join("");
    }
    pages.forEach((page, i) => {
      const el = pagesEl.children[i];
      el.className = "page " + page.status;
      el.title = `page ${page.number}${page.error ? ": " + page.error : ""}`;
    });
  }

  function renderState(state) {
    const p = state.progress;
    $("bar-fill").style.width = `${p.percent}%`;
    $("job-meta").textContent = `${state.status} · ${state.engine}`;
    $("job-status").textContent =
      `${p.percent.toFixed(1)}% · ${p.pages_done}/${p.pages_total} pages` +
      (p.pages_failed ? ` · ${p.pages_failed} failed` : "") +
      ` · ${p.message}` + fmtEta(p.eta_seconds);
    renderPages(state.pages || []);

    const finished = ["done", "failed", "cancelled"].includes(state.status);
    $("cancel").classList.toggle("hidden", finished);
    if (state.status === "done") showResult(state.id);
    if (state.status === "failed") {
      output.classList.remove("hidden");
      output.textContent = state.error || "job failed";
    }
    if (finished) refreshRecent();
  }

  async function showResult(id) {
    actions.classList.remove("hidden");
    $("dl-txt").href = `/api/jobs/${id}/result?format=txt&download=true`;
    $("dl-md").href = `/api/jobs/${id}/result?format=md&download=true`;
    $("dl-json").href = `/api/jobs/${id}/result?format=json`;
    const text = await fetch(`/api/jobs/${id}/result?format=txt`).then((r) => r.text());
    output.classList.remove("hidden");
    output.textContent = text || "(no text found)";
  }

  function follow(id) {
    if (source) source.close();
    source = new EventSource(`/api/jobs/${id}/events`);
    source.onmessage = (event) => {
      const state = JSON.parse(event.data);
      renderState(state);
      if (["done", "failed", "cancelled"].includes(state.status)) source.close();
    };
    // If the stream drops, fall back to polling so the UI never freezes mid-job.
    source.onerror = async () => {
      source.close();
      const state = await fetch(`/api/jobs/${id}`).then((r) => r.json());
      renderState(state);
      if (!["done", "failed", "cancelled"].includes(state.status)) setTimeout(() => follow(id), 2000);
    };
  }

  async function upload(file) {
    if (!file) return;
    if (file.type && file.type !== "application/pdf") {
      alert("Please pick a PDF file.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    body.append("engine", $("engine").value);
    body.append("dpi", $("dpi").value);

    jobPanel.classList.remove("hidden");
    actions.classList.add("hidden");
    output.classList.add("hidden");
    output.textContent = "";
    pagesEl.innerHTML = "";
    $("job-name").textContent = file.name;
    $("job-status").textContent = "uploading…";
    $("bar-fill").style.width = "0%";

    const response = await fetch("/api/jobs", { method: "POST", body });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: response.statusText }));
      $("job-status").textContent = `upload failed: ${detail.detail}`;
      return;
    }
    const job = await response.json();
    currentId = job.id;
    jobPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    follow(job.id);
  }

  async function refreshRecent() {
    const jobs = await fetch("/api/jobs").then((r) => r.json()).catch(() => []);
    const list = $("recent-list");
    if (!jobs.length) {
      list.innerHTML = '<li class="meta">none yet</li>';
      return;
    }
    list.innerHTML = jobs
      .slice(0, 10)
      .map((job) => {
        const link =
          job.status === "done"
            ? `<a href="/api/jobs/${job.id}/result?format=txt&download=true">download</a>`
            : `<span class="meta">${job.progress.percent.toFixed(0)}%</span>`;
        return `<li><span>${job.filename} <span class="meta">· ${job.status} · ${job.engine}</span></span>${link}</li>`;
      })
      .join("");
  }

  // -- wiring ---------------------------------------------------------------
  $("browse").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => upload(fileInput.files[0]));
  $("again").addEventListener("click", () => {
    fileInput.value = "";
    jobPanel.classList.add("hidden");
  });
  $("cancel").addEventListener("click", () => currentId && fetch(`/api/jobs/${currentId}/cancel`, { method: "POST" }));
  $("copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText(output.textContent);
    $("copy").textContent = "Copied";
    setTimeout(() => ($("copy").textContent = "Copy text"), 1500);
  });

  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.add("hover");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, (e) => {
      e.preventDefault();
      drop.classList.remove("hover");
    })
  );
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));

  loadEngines();
  refreshRecent();
})();
