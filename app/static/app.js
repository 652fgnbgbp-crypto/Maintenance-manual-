"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const COLORS = ["var(--c0)", "var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];
const colorFor = (rank) => COLORS[rank >= 1 && rank <= 5 ? rank : rank > 5 ? 5 : 0];

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function md(text) {
  // Small, safe markdown subset: paragraphs, bullet lists, **bold**, `code`.
  const lines = esc(text || "").split(/\n/);
  let html = "", inList = false, para = [];
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const flush = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  for (const line of lines) {
    const m = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.*)/);
    if (m) { flush(); if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(m[1])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (!line.trim()) flush(); else para.push(line.replace(/^#+\s*/, ""));
  }
  flush(); if (inList) html += "</ul>";
  return html;
}

// ===================================================================== tabs
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "library") refreshDocs();
  if (name === "notes") refreshNotes();
  if (name === "history") refreshHistory();
}

async function refreshInfo() {
  const info = await api("/api/info");
  $("#aircraftType").textContent = info.aircraft_type;
  $("#stats").textContent = `${info.documents} documents · ${info.pages} pages · ${info.notes} notes`;
  return info;
}

// ===================================================================== page viewer
function createViewer(root) {
  let zoom = 1, current = null;
  root.innerHTML = `<div class="zoom"><button data-z="-1" title="Zoom out">−</button><button data-z="0" title="Fit width">Fit</button><button data-z="1" title="Zoom in">+</button></div><div class="page"></div>`;
  const pageEl = $(".page", root);
  $$(".zoom button", root).forEach((b) => b.addEventListener("click", () => {
    const z = +b.dataset.z;
    zoom = z === 0 ? 1 : Math.min(5, Math.max(0.5, zoom * (z > 0 ? 1.4 : 1 / 1.4)));
    pageEl.style.width = `${zoom * 100}%`;
  }));

  function overlay(highlights, regions) {
    let svg = `<svg viewBox="0 0 1000 1000" preserveAspectRatio="none">`;
    const pad = 4;
    (regions || []).forEach((r, i) => {
      svg += `<rect class="hl region" data-key="r${i}" x="${r.x * 1000}" y="${r.y * 1000}" width="${r.w * 1000}" height="${r.h * 1000}" rx="6" fill="${colorFor(r.cause_rank)}" stroke="${colorFor(r.cause_rank)}"><title>${esc(r.note)}</title></rect>`;
    });
    (highlights || []).forEach((h, i) => {
      (h.boxes || []).forEach((b) => {
        svg += `<rect class="hl" data-key="h${i}" x="${b.x * 1000 - pad}" y="${b.y * 1000 - pad}" width="${b.w * 1000 + pad * 2}" height="${b.h * 1000 + pad * 2}" rx="4" fill="${colorFor(h.cause_rank)}" stroke="${colorFor(h.cause_rank)}"><title>${esc(h.term)}${h.note ? " — " + esc(h.note) : ""}</title></rect>`;
      });
    });
    return svg + "</svg>";
  }

  async function show(docId, pageNo, highlights = [], regions = [], textPage = null) {
    current = { docId, pageNo };
    if (textPage !== null) {
      pageEl.className = "page textpage";
      let t = esc(textPage);
      (highlights || []).forEach((h) => {
        if (!h.term) return;
        const re = new RegExp(h.term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
        t = t.replace(re, (m) => `<mark>${m}</mark>`);
      });
      pageEl.innerHTML = t;
      return;
    }
    pageEl.className = "page";
    pageEl.innerHTML = `<img alt="Page ${pageNo}" src="/api/pages/${docId}/${pageNo}/image">${overlay(highlights, regions)}`;
    // Scroll to the first highlight once the image has a size.
    const img = $("img", pageEl);
    img.addEventListener("load", () => {
      const first = $("rect.hl", pageEl);
      if (first && zoom > 1) pulse(first.dataset.key);
    }, { once: true });
  }

  function pulse(key) {
    const rects = $$(`rect[data-key="${key}"]`, pageEl);
    rects.forEach((r) => { r.classList.remove("pulse"); void r.getBoundingClientRect(); r.classList.add("pulse"); });
    if (rects[0]) {  // scroll only the viewer, not the whole page
      const a = rects[0].getBoundingClientRect(), v = root.getBoundingClientRect();
      root.scrollBy({ left: a.left - v.left - v.width / 2 + a.width / 2, top: a.top - v.top - v.height / 2 + a.height / 2, behavior: "smooth" });
    }
  }
  return { show, pulse, get current() { return current; } };
}

const diagViewer = createViewer($("#diagViewer"));
const libViewer = createViewer($("#libViewer"));

// ===================================================================== diagnose
let sessionId = null;
let currentDiagnosisId = null;
let currentRefs = [];

$("#diagForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#question").value.trim();
  if (!q) return $("#question").focus();
  sessionId = null;
  $("#result").innerHTML = "";
  $("#result").classList.add("hidden");
  $("#followForm").classList.add("hidden");
  runDiagnosis(q);
});

$("#followForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#followup").value.trim();
  if (!q) return;
  $("#followup").value = "";
  runDiagnosis(q);
});

async function runDiagnosis(question) {
  const btn = $("#diagBtn");
  btn.disabled = true;
  $$("#followForm button").forEach((b) => (b.disabled = true));
  $("#progress").classList.remove("hidden");
  $("#spinner").classList.remove("stopped");
  $("#progressTitle").textContent = "Researching the maintenance data…";
  $("#trace").innerHTML = "";
  try {
    const res = await fetch("/api/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, serial: $("#serial").value, session_id: sessionId }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) handleEvent(JSON.parse(line.slice(6)));
      }
    }
  } catch (err) {
    handleEvent({ type: "error", text: err.message });
  } finally {
    btn.disabled = false;
    $$("#followForm button").forEach((b) => (b.disabled = false));
    $("#spinner").classList.add("stopped");
    refreshInfo();
  }
}

function addTrace(step) {
  const li = document.createElement("li");
  li.className = step.kind;
  if (step.kind === "thinking" || step.kind === "text") {
    const short = step.text.length > 160 ? step.text.slice(0, 160) + "…" : step.text;
    li.innerHTML = step.text.length > 160 ? `<details><summary>${esc(short)}</summary><div class="md">${md(step.text)}</div></details>` : esc(step.text);
  } else if (step.kind === "view" || step.kind === "locate") {
    li.innerHTML = `<a>${esc(step.text)}</a>`;
    $("a", li).addEventListener("click", () => {
      $$(".ref-tab").forEach((t) => t.classList.remove("active"));
      $("#refWhy").textContent = "";
      $("#refLegend").innerHTML = "";
      diagViewer.show(step.doc_id, step.page);
    });
  } else {
    li.textContent = step.text;
  }
  $("#trace").appendChild(li);
  $("#trace").scrollTop = $("#trace").scrollHeight;
}

function handleEvent(ev) {
  if (ev.type === "session") sessionId = ev.session_id;
  else if (ev.type === "status") $("#progressTitle").textContent = ev.text;
  else if (ev.type === "step") addTrace(ev);
  else if (ev.type === "error") {
    $("#progressTitle").textContent = "Stopped";
    const li = document.createElement("li");
    li.innerHTML = `<b style="color:var(--danger)">${esc(ev.text)}</b>`;
    $("#trace").appendChild(li);
  } else if (ev.type === "done") {
    sessionId = ev.session_id;
    $("#progressTitle").textContent = `Done — ${ev.trace.filter((s) => s.kind === "view").length} pages reviewed`;
    if (ev.type === "done" && ev.result) renderDiagnosis(ev.result, ev.diagnosis_id, null);
    else renderMessage(ev.text);
    $("#followForm").classList.remove("hidden");
  }
}

function renderMessage(text) {
  const el = $("#result");
  el.classList.remove("hidden");
  el.insertAdjacentHTML("beforeend", `<div class="card md">${md(text)}</div>`);
}

function renderDiagnosis(r, diagnosisId, resolution) {
  currentDiagnosisId = diagnosisId;
  currentRefs = r.references || [];
  const el = $("#result");
  el.classList.remove("hidden");
  const causes = (r.probable_causes || []).sort((a, b) => a.rank - b.rank);
  const refLink = (ref, i) => `<li><a data-ref="${i}">${esc(ref.ata || "")} ${ref.printed_page ? "p." + esc(ref.printed_page) : ""} ${esc(ref.subject || "")}</a> <span class="muted">— ${esc(ref.document)}, file page ${ref.page}${ref.effectivity ? ", " + esc(ref.effectivity) : ""}</span>${ref.verified ? "" : ' <span class="badge-unverified">(not opened by the AI)</span>'}<br>${esc(ref.why)}</li>`;

  el.innerHTML = `
    <div class="card">
      <div class="summary-head"><span class="system">${esc(r.system)}</span><span class="chip ${esc(r.confidence)}">${esc(r.confidence)} confidence</span></div>
      <div class="summary">${md(r.summary)}</div>
      ${r.effectivity_note ? `<div class="muted" style="font-size:12.5px">Effectivity: ${esc(r.effectivity_note)}</div>` : ""}
    </div>
    <div class="card">
      <div class="card-title">Probable causes</div>
      ${causes.map((c) => `<div class="cause"><div class="rank" style="background:${colorFor(c.rank)}">${c.rank}</div><div><div class="cause-name">${esc(c.cause)} <span class="chip ${esc(c.likelihood)}">${esc(c.likelihood)}</span></div><p>${esc(c.reasoning)}</p></div></div>`).join("")}
    </div>
    <div class="card">
      <div class="card-title">Isolation steps</div>
      <table class="steps"><tr><th>#</th><th>Check</th><th>Normal</th><th>If abnormal</th></tr>
      ${(r.troubleshooting_steps || []).map((s) => `<tr><td><b>${s.step}</b><div class="dots">${(s.cause_ranks || []).map((k) => `<span class="dot" title="Tests cause ${k}" style="background:${colorFor(k)}"></span>`).join("")}</div></td><td>${esc(s.action)}</td><td>${esc(s.expected)}</td><td>${esc(s.if_abnormal)}</td></tr>`).join("")}
      </table>
    </div>
    <div class="card">
      <div class="card-title">How this was diagnosed</div>
      <div class="md">${md(r.reasoning)}</div>
      <div class="card-title" style="margin-top:10px">References</div>
      <ul class="plain refs-inline">${currentRefs.map(refLink).join("") || "<li class='muted'>No pages cited.</li>"}</ul>
    </div>
    ${(r.safety_notes || []).length ? `<div class="card safety"><div class="card-title">Safety</div><ul class="plain">${r.safety_notes.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
    ${(r.missing_info || []).length ? `<div class="card"><div class="card-title">Would help narrow it down</div><ul class="plain">${r.missing_info.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
    <div class="card">
      <div class="card-title">Record the confirmed fix</div>
      ${resolution ? `<div class="fixed">✓ Recorded: ${esc(resolution)}</div>` : `
      <div class="muted" style="font-size:12.5px">Once the aircraft is fixed, record what it actually was. It is saved as a troubleshooting note and used in future diagnoses.</div>
      <div class="row"><input id="resolution" placeholder="e.g. Replaced K45 — coil open circuit"><button id="saveResolution">Save</button></div>`}
    </div>`;

  $$("a[data-ref]", el).forEach((a) => a.addEventListener("click", () => selectRef(+a.dataset.ref)));
  const save = $("#saveResolution");
  if (save) save.addEventListener("click", async () => {
    const v = $("#resolution").value.trim();
    if (!v) return;
    await api(`/api/diagnoses/${currentDiagnosisId}/resolution`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resolution: v }) });
    save.parentElement.outerHTML = `<div class="fixed">✓ Recorded: ${esc(v)}</div>`;
    refreshInfo();
  });

  renderRefTabs();
  if (currentRefs.length) selectRef(0);
}

function renderRefTabs() {
  const tabs = $("#refTabs");
  if (!currentRefs.length) { tabs.innerHTML = `<div class="empty">No diagram references for this diagnosis.</div>`; return; }
  tabs.innerHTML = currentRefs.map((r, i) => `<button class="ref-tab" data-i="${i}">${esc(r.ata || r.document)} ${r.printed_page ? "p." + esc(r.printed_page) : ""}<small>${esc((r.subject || "").slice(0, 34))}</small></button>`).join("");
  $$(".ref-tab", tabs).forEach((b) => b.addEventListener("click", () => selectRef(+b.dataset.i)));
}

function selectRef(i) {
  const r = currentRefs[i];
  if (!r) return;
  $$(".ref-tab").forEach((b) => b.classList.toggle("active", +b.dataset.i === i));
  $("#refWhy").innerHTML = `<b>${esc(r.document)}</b> · file page ${r.page}${r.effectivity ? " · " + esc(r.effectivity) : ""}<br>${esc(r.why)}`;
  diagViewer.show(r.doc_id, r.page, r.highlights, r.regions, r.has_image ? null : r.text || "");
  const items = [
    ...r.highlights.map((h, k) => ({ key: `h${k}`, label: h.term, note: h.note, rank: h.cause_rank, found: (h.boxes || []).length > 0 })),
    ...r.regions.map((g, k) => ({ key: `r${k}`, label: "Area", note: g.note, rank: g.cause_rank, found: true })),
  ];
  $("#refLegend").innerHTML = items.map((it) => `<span class="item ${it.found ? "" : "missing"}" data-key="${it.key}" title="${esc(it.note)}${it.found ? "" : " (not found on the OCR layer)"}"><span class="sw" style="background:${colorFor(it.rank)}"></span>${esc(it.label)}</span>`).join("");
  $$("#refLegend .item:not(.missing)").forEach((x) => x.addEventListener("click", () => diagViewer.pulse(x.dataset.key)));
  if (window.innerWidth < 1000) $("#diagViewer").scrollIntoView({ behavior: "smooth" });
}

// ===================================================================== library
let docsCache = [];
let pollTimer = null;

$("#uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = $("#file").files;
  if (!files.length) return;
  const msg = $("#uploadMsg");
  msg.className = "msg";
  for (const f of files) {
    msg.textContent = `Uploading ${f.name}…`;
    const fd = new FormData();
    fd.append("file", f);
    fd.append("title", files.length === 1 ? $("#docTitle").value : "");
    fd.append("category", $("#category").value);
    fd.append("enrich_with_ai", $("#enrichAI").checked ? "true" : "false");
    try {
      await api("/api/documents", { method: "POST", body: fd });
    } catch (err) {
      msg.className = "msg err"; msg.textContent = `${f.name}: ${err.message}`; return;
    }
  }
  msg.textContent = "Uploaded. Pages are being indexed.";
  $("#uploadForm").reset();
  refreshDocs();
});

async function refreshDocs() {
  docsCache = await api("/api/documents");
  const list = $("#docList");
  list.innerHTML = docsCache.length ? docsCache.map((d) => {
    const pct = d.page_count ? Math.round((100 * d.enrich_done) / d.page_count) : 0;
    const status = d.status === "processing" ? "Indexing…" : d.status === "error" ? `Error: ${esc(d.error)}` :
      `${d.page_count} pages · ${esc(d.category)}` + (d.enrich_status === "done" ? " · AI-read" : d.enrich_status === "running" ? ` · AI reading ${pct}%` : d.enrich_status === "error" ? " · AI read partly failed" : "");
    return `<div class="doc"><div><div class="title">${esc(d.title)}</div><div class="meta">${status}</div>${d.enrich_status === "running" ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}</div>
      <div class="actions">${d.status === "ready" ? `<button class="small" data-browse="${d.id}">Browse</button>${d.enrich_status !== "running" && d.enrich_status !== "done" ? `<button class="small" data-enrich="${d.id}" title="Have AI read every page to improve search">AI-read</button>` : ""}` : ""}<button class="small danger" data-del="${d.id}" title="Delete">✕</button></div></div>`;
  }).join("") : `<div class="muted">No documents yet. Upload your manuals, wiring diagrams and notes above.</div>`;

  $$("[data-browse]", list).forEach((b) => b.addEventListener("click", () => openBrowse(+b.dataset.browse, 1)));
  $$("[data-enrich]", list).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Have the AI read every page of this document? This improves search on scanned pages and uses API credits (roughly one request per page).")) return;
    await api(`/api/documents/${b.dataset.enrich}/enrich`, { method: "POST" });
    refreshDocs();
  }));
  $$("[data-del]", list).forEach((b) => b.addEventListener("click", async () => {
    const d = docsCache.find((x) => x.id === +b.dataset.del);
    if (!confirm(`Delete "${d.title}" from the library?`)) return;
    await api(`/api/documents/${d.id}`, { method: "DELETE" });
    refreshDocs(); refreshInfo();
  }));

  const sel = $("#browseDoc");
  const prev = sel.value;
  sel.innerHTML = docsCache.filter((d) => d.status === "ready").map((d) => `<option value="${d.id}">${esc(d.title)}</option>`).join("");
  if (prev && docsCache.some((d) => d.id === +prev)) sel.value = prev;
  if (!libViewer.current && sel.value) openBrowse(+sel.value, 1);

  clearTimeout(pollTimer);
  if (docsCache.some((d) => d.status === "processing" || d.enrich_status === "running")) pollTimer = setTimeout(() => { refreshDocs(); refreshInfo(); }, 2500);
}

let browsePages = [];
async function openBrowse(docId, pageNo, highlights = []) {
  showTab("library");
  $("#browseDoc").value = docId;
  browsePages = await api(`/api/documents/${docId}/pages`);
  $("#browsePage").innerHTML = browsePages.map((p) => `<option value="${p.page_no}">${p.page_no}. ${esc([p.ata, p.page_label ? "p." + p.page_label : "", p.subject].filter(Boolean).join(" "))}</option>`).join("");
  await showBrowsePage(pageNo, highlights);
}

async function showBrowsePage(pageNo, highlights = []) {
  const docId = +$("#browseDoc").value;
  $("#browsePage").value = pageNo;
  const p = await api(`/api/pages/${docId}/${pageNo}`);
  $("#browseMeta").textContent = [p.doc_title, `file page ${p.page_no} of ${p.page_count}`, p.ata, p.page_label && `printed page ${p.page_label}`, p.subject, p.effectivity, p.page_type].filter(Boolean).join(" · ") + (p.ai_summary ? ` — ${p.ai_summary}` : "");
  libViewer.show(docId, pageNo, highlights, [], p.has_image ? null : p.text);
}

$("#browseDoc").addEventListener("change", () => openBrowse(+$("#browseDoc").value, 1));
$("#browsePage").addEventListener("change", () => showBrowsePage(+$("#browsePage").value));
$("#prevPage").addEventListener("click", () => { const v = +$("#browsePage").value; if (v > 1) showBrowsePage(v - 1); });
$("#nextPage").addEventListener("click", () => { const v = +$("#browsePage").value; if (v < browsePages.length) showBrowsePage(v + 1); });

$("#findForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const cur = libViewer.current;
  if (!cur) return;
  const terms = $("#findTerms").value.split(",").map((t) => t.trim()).filter(Boolean);
  const found = await api(`/api/pages/${cur.docId}/${cur.pageNo}/locate`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ terms }) });
  const hl = terms.map((t, i) => ({ term: t, note: `${found[t].length} found`, cause_rank: (i % 5) + 1, boxes: found[t] }));
  const p = await api(`/api/pages/${cur.docId}/${cur.pageNo}`);
  libViewer.show(cur.docId, cur.pageNo, hl, [], p.has_image ? null : p.text);
  $("#browseMeta").textContent = terms.map((t) => `${t}: ${found[t].length} found`).join(" · ");
});

$("#libSearchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#libSearch").value.trim();
  if (!q) return;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
  const out = $("#libResults");
  const pages = res.pages.map((p) => `<div class="hit" data-doc="${p.doc_id}" data-page="${p.page_no}"><div class="h-title">${esc([p.ata, p.page_label && "p." + p.page_label, p.subject].filter(Boolean).join(" ") || "Page " + p.page_no)}</div><div class="h-snip">${esc(p.doc_title)} · file page ${p.page_no}${p.effectivity ? " · " + esc(p.effectivity) : ""}<br>${esc(p.snippet || "")}</div></div>`).join("");
  const notes = res.notes.map((n) => `<div class="hit" data-note="${n.id}"><div class="h-title">Note: ${esc(n.title)}</div><div class="h-snip">${esc(n.body.slice(0, 200))}</div></div>`).join("");
  out.innerHTML = pages + notes || `<div class="muted">No matches.</div>`;
  const terms = q.split(/[\s,]+/).filter((t) => t.length > 1);
  $$(".hit[data-doc]", out).forEach((h) => h.addEventListener("click", async () => {
    await openBrowse(+h.dataset.doc, +h.dataset.page);
    $("#findTerms").value = terms.join(", ");
    $("#findForm").requestSubmit();
  }));
  $$(".hit[data-note]", out).forEach((h) => h.addEventListener("click", () => showTab("notes")));
});

// ===================================================================== notes
$("#noteForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/notes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      title: $("#noteTitle").value, body: $("#noteBody").value, tags: $("#noteTags").value, serial: $("#noteSerial").value }) });
  } catch (err) { alert(err.message); return; }
  $("#noteForm").reset();
  refreshNotes(); refreshInfo();
});

async function refreshNotes() {
  const notes = await api("/api/notes");
  $("#noteList").innerHTML = notes.length ? notes.map((n) => `<div class="note"><div class="n-head"><div><b>${esc(n.title)}</b><div class="muted" style="font-size:12px">${esc(n.created_at)}${n.serial ? " · " + esc(n.serial) : ""}${n.tags ? " · " + esc(n.tags) : ""}${n.source === "diagnosis" ? " · from a diagnosis" : ""}</div></div><button class="small danger" data-del="${n.id}" title="Delete">✕</button></div><div class="n-body">${esc(n.body)}</div></div>`).join("")
    : `<div class="muted">No notes yet. Add snags, known issues and fixes; the AI checks them during each diagnosis.</div>`;
  $$("#noteList [data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this note?")) return;
    await api(`/api/notes/${b.dataset.del}`, { method: "DELETE" });
    refreshNotes(); refreshInfo();
  }));
}

// ===================================================================== history
async function refreshHistory() {
  const rows = await api("/api/diagnoses");
  $("#historyList").innerHTML = rows.length ? rows.map((r) => `<div class="hist" data-id="${r.id}"><div class="h-title">${esc(r.question.slice(0, 140))}</div><div class="muted" style="font-size:12.5px">${esc(r.created_at)}${r.serial ? " · S/N " + esc(r.serial) : ""} · ${esc(r.system)}</div>${r.resolution ? `<div class="fixed">✓ Fixed: ${esc(r.resolution)}</div>` : ""}</div>`).join("")
    : `<div class="muted">No diagnoses yet.</div>`;
  $$("#historyList .hist").forEach((h) => h.addEventListener("click", async () => {
    const d = await api(`/api/diagnoses/${h.dataset.id}`);
    showTab("diagnose");
    sessionId = null;
    $("#question").value = d.question.replace(/^\(follow-up\) /, "");
    $("#serial").value = d.serial;
    $("#progress").classList.remove("hidden");
    $("#spinner").classList.add("stopped");
    $("#progressTitle").textContent = `Saved diagnosis from ${d.created_at}`;
    $("#trace").innerHTML = "";
    d.trace.forEach(addTrace);
    $("#followForm").classList.add("hidden");
    renderDiagnosis(d.result, d.id, d.resolution);
  }));
}

// ===================================================================== start
refreshInfo().then((info) => {
  if (!info.api_key_configured) {
    $("#tab-diagnose").insertAdjacentHTML("afterbegin", `<div class="notice">No <code>ANTHROPIC_API_KEY</code> was found on the server. The library, search and notes work without one, but diagnosis needs it: set it and restart the server.</div>`);
  }
  if (!info.documents) showTab("library");
});
