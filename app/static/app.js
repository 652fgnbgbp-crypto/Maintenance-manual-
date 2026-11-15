"use strict";
/* Vantage: browser app. Works with touch (iPad) and mouse. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const COLORS = ["var(--c0)", "var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];
const colorFor = (rank) => COLORS[rank >= 1 && rank <= 5 ? rank : rank > 5 ? 5 : 0];
const narrow = () => window.matchMedia("(max-width: 1023px)").matches;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("Please sign in."); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function md(text) {
  // Small, safe markdown subset: paragraphs, bullet/numbered lists, **bold**, `code`.
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

// ===================================================================== navigation
$$(".nav-btn").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
function showTab(name) {
  $$(".nav-btn").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "library") refreshDocs();
  if (name === "fixes") refreshFixes();
  if (name === "team") refreshOrg();
  if (name === "history") refreshHistory();
  requestAnimationFrame(() => { diagViewer.refit(); libViewer.refit(); });
}

async function signOut() {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
}
$("#signOutTop").addEventListener("click", signOut);

async function refreshInfo() {
  const info = await api("/api/info");
  $("#orgNameRail").textContent = info.organisation;
  $("#aircraftType").textContent = info.aircraft_type;
  $("#aircraftTypeTop").textContent = `${info.organisation} · ${info.aircraft_type}`;
  const n = (k, word) => `${k} ${word}${k === 1 ? "" : "s"}`;
  $("#stats").textContent = `${n(info.documents, "document")} · ${n(info.pages, "page")} · ${n(info.fixes, "fix")}`.replace("fixs", "fixes");
  document.body.classList.toggle("not-admin", !info.is_admin);
  $$(".platform-only").forEach((e) => e.classList.toggle("hidden", !info.is_platform_admin));
  if (info.org_status === "suspended") {
    $("#keyNotice").innerHTML = `<div class="notice">This organisation's Vantage subscription is not active. Contact Torwood Aerospace Group to restore access.</div>`;
  }
  if (info.user) {
    $("#userBox").innerHTML = `<span>${esc(info.user.name)}</span><button title="Sign out"><svg><use href="#i-signout"/></svg></button>`;
    $("#userBox button").addEventListener("click", signOut);
    $("#signOutTop").classList.remove("hidden");
  }
  return info;
}

// ===================================================================== diagram viewer (pinch, pan, double-tap)
function createViewer(root) {
  root.innerHTML = `<div class="stage"></div>
    <div class="vctl">
      <button data-a="full" title="Full screen"><svg><use href="#i-expand"/></svg></button>
      <button data-a="in" title="Zoom in"><svg><use href="#i-plus"/></svg></button>
      <button data-a="out" title="Zoom out"><svg><use href="#i-minus"/></svg></button>
      <button data-a="fit" title="Whole page"><svg><use href="#i-fit"/></svg></button>
    </div>
    <div class="vtip hidden"></div>`;
  const stage = $(".stage", root), tip = $(".vtip", root), card = root.closest(".viewer-card");
  let s = 1, tx = 0, ty = 0, w = 0, h = 0, fitS = 1, current = null;

  const apply = () => { stage.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`; };
  function clamp() {
    const vw = root.clientWidth, vh = root.clientHeight, sw = w * s, sh = h * s, m = 40;
    tx = sw <= vw ? (vw - sw) / 2 : Math.min(m, Math.max(vw - sw - m, tx));
    ty = sh <= vh ? (vh - sh) / 2 : Math.min(m, Math.max(vh - sh - m, ty));
  }
  function fit() {
    if (!w) return;
    fitS = Math.min(root.clientWidth / w, root.clientHeight / h) * 0.97;
    s = fitS; tx = 0; ty = 0; clamp(); apply();
  }
  function zoomAt(factor, cx, cy) {
    const ns = Math.min(fitS * 10, Math.max(fitS * 0.9, s * factor));
    tx = cx - (cx - tx) * (ns / s); ty = cy - (cy - ty) * (ns / s); s = ns;
    clamp(); apply();
  }
  function focusBox(b) {
    // centre a 0..1 box and zoom so it is comfortably readable
    const vw = root.clientWidth, vh = root.clientHeight;
    s = Math.max(s, Math.min(fitS * 3.5, (vw * 0.35) / Math.max(1, b.w * w)));
    tx = vw / 2 - (b.x + b.w / 2) * w * s; ty = vh / 2 - (b.y + b.h / 2) * h * s;
    clamp(); stage.style.transition = "transform .35s ease"; apply();
    setTimeout(() => (stage.style.transition = ""), 380);
  }

  // ---- pointer gestures
  const pts = new Map();
  let pinch = null, pan = null, moved = 0, lastTap = 0;
  const local = (e) => { const r = root.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  root.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".vctl")) return;
    root.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, local(e));
    moved = 0;
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), s, tx, ty, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
      pan = null;
    } else if (pts.size === 1) {
      const p = local(e); pan = { x: p.x, y: p.y, tx, ty };
    }
  });
  root.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, local(e));
    if (pinch && pts.size >= 2) {
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y), cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      const ns = Math.min(fitS * 10, Math.max(fitS * 0.9, pinch.s * (d / pinch.d)));
      tx = cx - (pinch.cx - pinch.tx) * (ns / pinch.s); ty = cy - (pinch.cy - pinch.ty) * (ns / pinch.s); s = ns;
      clamp(); apply(); moved = 99;
    } else if (pan) {
      const p = local(e);
      moved = Math.max(moved, Math.hypot(p.x - pan.x, p.y - pan.y));
      tx = pan.tx + (p.x - pan.x); ty = pan.ty + (p.y - pan.y); clamp(); apply();
    }
  });
  const end = (e) => {
    if (!pts.has(e.pointerId)) return;
    const p = pts.get(e.pointerId);
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (pts.size === 1) { const q = [...pts.values()][0]; pan = { x: q.x, y: q.y, tx, ty }; } else pan = null;
    if (e.type === "pointerup" && moved < 8 && pts.size === 0) {
      const now = Date.now();
      if (now - lastTap < 320) { // double tap: zoom in, or back to whole page
        lastTap = 0;
        if (s > fitS * 1.6) fit(); else zoomAt(2.5, p.x, p.y);
      } else {
        lastTap = now;
        showTip(e, p);
      }
    }
  };
  root.addEventListener("pointerup", end);
  root.addEventListener("pointercancel", end);
  root.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = local(e);
    if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY / 200), p.x, p.y);
    else { tx -= e.deltaX; ty -= e.deltaY; clamp(); apply(); }
  }, { passive: false });
  root.addEventListener("gesturestart", (e) => e.preventDefault()); // stop Safari zooming the whole page

  function showTip(e, p) {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const rect = el && el.closest && el.closest("rect.hl");
    if (!rect) { tip.classList.add("hidden"); return; }
    tip.innerHTML = `<b>${esc(rect.dataset.label)}</b>${esc(rect.dataset.note || "")}`;
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(p.x + 12, root.clientWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${Math.max(8, p.y - tip.offsetHeight - 12)}px`;
  }

  $$(".vctl button", root).forEach((b) => b.addEventListener("click", () => {
    const a = b.dataset.a, cx = root.clientWidth / 2, cy = root.clientHeight / 2;
    if (a === "in") zoomAt(1.5, cx, cy);
    else if (a === "out") zoomAt(1 / 1.5, cx, cy);
    else if (a === "fit") fit();
    else if (a === "full") setFull(!card.classList.contains("fullscreen"));
  }));
  function setFull(on) {
    card.classList.toggle("fullscreen", on);
    requestAnimationFrame(fit);
  }
  $(".close-full", card).addEventListener("click", () => setFull(false));
  new ResizeObserver(() => { if (w) fit(); }).observe(root);

  function overlay(highlights, regions) {
    const pad = 4;
    let svg = `<svg viewBox="0 0 1000 1000" preserveAspectRatio="none">`;
    (regions || []).forEach((r, i) => {
      svg += `<rect class="hl region" data-key="r${i}" data-label="Area" data-note="${esc(r.note)}" x="${r.x * 1000}" y="${r.y * 1000}" width="${r.w * 1000}" height="${r.h * 1000}" rx="6" fill="${colorFor(r.cause_rank)}" stroke="${colorFor(r.cause_rank)}"/>`;
    });
    (highlights || []).forEach((hl, i) => {
      (hl.boxes || []).forEach((b) => {
        svg += `<rect class="hl" data-key="h${i}" data-label="${esc(hl.term)}" data-note="${esc(hl.note || "")}" x="${b.x * 1000 - pad}" y="${b.y * 1000 - pad}" width="${b.w * 1000 + pad * 2}" height="${b.h * 1000 + pad * 2}" rx="4" fill="${colorFor(hl.cause_rank)}" stroke="${colorFor(hl.cause_rank)}"/>`;
      });
    });
    return svg + "</svg>";
  }

  async function show(docId, pageNo, highlights = [], regions = [], textPage = null) {
    current = { docId, pageNo, highlights, regions };
    tip.classList.add("hidden");
    if (textPage !== null) {
      let t = esc(textPage);
      (highlights || []).forEach((hl) => {
        if (!hl.term) return;
        const re = new RegExp(esc(hl.term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
        t = t.replace(re, (m) => `<mark>${m}</mark>`);
      });
      stage.innerHTML = `<div class="textpage">${t}</div>`;
      w = stage.firstChild.offsetWidth; h = stage.firstChild.offsetHeight;
      stage.style.width = `${w}px`; stage.style.height = `${h}px`;
      fit(); s = Math.min(1, root.clientWidth / w); tx = 0; ty = 0; clamp(); apply();
      return;
    }
    const img = new Image();
    img.alt = `Page ${pageNo}`;
    img.src = `/api/pages/${docId}/${pageNo}/image`;
    await img.decode().catch(() => {});
    if (current.docId !== docId || current.pageNo !== pageNo) return; // a newer page was requested
    w = img.naturalWidth || 1000; h = img.naturalHeight || 1300;
    stage.style.width = `${w}px`; stage.style.height = `${h}px`;
    stage.innerHTML = "";
    stage.appendChild(img);
    stage.insertAdjacentHTML("beforeend", overlay(highlights, regions));
    fit();
  }

  function focus(key) {
    const rects = $$(`rect[data-key="${key}"]`, stage);
    if (!rects.length) return;
    rects.forEach((r) => { r.classList.remove("pulse"); void r.getBoundingClientRect(); r.classList.add("pulse"); });
    const r = rects[0];
    focusBox({ x: r.x.baseVal.value / 1000, y: r.y.baseVal.value / 1000, w: r.width.baseVal.value / 1000, h: r.height.baseVal.value / 1000 });
  }
  return { show, focus, fit, setFull, refit: () => w && fit(), get current() { return current; } };
}

const diagViewer = createViewer($("#diagViewer"));
const libViewer = createViewer($("#libViewer"));

// ===================================================================== diagnose
let sessionId = null;
let currentDiagnosisId = null;
let currentRefs = [];

$("#serial").value = store.get("vantage.serial") || "";
$("#serial").addEventListener("change", () => store.set("vantage.serial", $("#serial").value.trim()));

$("#diagForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#question").value.trim();
  if (!q) return $("#question").focus();
  sessionId = null;
  clearResult();
  runDiagnosis(q);
});

$("#followForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#followup").value.trim();
  if (!q) return;
  $("#followup").value = "";
  runDiagnosis(q);
});

$("#newDiag").addEventListener("click", () => {
  sessionId = null;
  $("#question").value = "";
  clearResult();
  $("#progress").classList.add("hidden");
  $("#newDiag").classList.add("hidden");
  $("#diagLeft").scrollTop = 0;
  $("#question").focus();
});

function clearResult() {
  $("#result").innerHTML = "";
  $("#result").classList.add("hidden");
  $("#followForm").classList.add("hidden");
  currentRefs = [];
  $("#refTabs").innerHTML = ""; $("#refWhy").innerHTML = ""; $("#refLegend").innerHTML = "";
  $("#viewerEmpty").classList.remove("hidden");
}

async function runDiagnosis(question) {
  const btn = $("#diagBtn");
  btn.disabled = true;
  $$("#followForm button").forEach((b) => (b.disabled = true));
  $("#progress").classList.remove("hidden");
  $("#newDiag").classList.remove("hidden");
  $("#spinner").classList.remove("stopped");
  $("#progressTitle").textContent = "Researching your maintenance data…";
  $("#trace").innerHTML = "";
  if (narrow()) $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const res = await fetch("/api/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, serial: $("#serial").value, session_id: sessionId }),
    });
    if (res.status === 401) { location.href = "/login"; return; }
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
      $("#refWhy").textContent = ""; $("#refLegend").innerHTML = "";
      $("#viewerEmpty").classList.add("hidden");
      diagViewer.show(step.doc_id, step.page);
      if (narrow()) diagViewer.setFull(true);
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
    $("#progressTitle").textContent = `Done: ${ev.trace.filter((s) => s.kind === "view").length} pages reviewed`;
    if (ev.result) renderDiagnosis(ev.result, ev.diagnosis_id, null);
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
  const causes = (r.probable_causes || []).slice().sort((a, b) => a.rank - b.rank);
  const fixes = r.previous_fixes || [];
  const refBtn = (ref, i) => `<li><button data-ref="${i}"><b>${esc([ref.ata, ref.printed_page && "p." + ref.printed_page, ref.subject].filter(Boolean).join(" "))}</b>
    <span class="muted">· ${esc(ref.document)}, file page ${ref.page}${ref.effectivity ? ", " + esc(ref.effectivity) : ""}</span>${ref.verified ? "" : ' <span class="badge-unverified">(not opened by the AI)</span>'}<br>${esc(ref.why)}</button></li>`;

  el.innerHTML = `
    <div class="card">
      <div class="summary-head"><span class="system">${esc(r.system)}</span><span class="chip ${esc(r.confidence)}">${esc(r.confidence)} confidence</span></div>
      <div class="summary md">${md(r.summary)}</div>
      ${r.effectivity_note ? `<div class="hint">Effectivity: ${esc(r.effectivity_note)}</div>` : ""}
    </div>
    ${fixes.length ? resolvedBox(fixes) : ""}
    <div class="card">
      <div class="card-title">Probable causes</div>
      ${causes.map((c) => `<div class="cause"><div class="rank" style="background:${colorFor(c.rank)}">${c.rank}</div><div><div class="cause-name">${esc(c.cause)} <span class="chip ${esc(c.likelihood)}">${esc(c.likelihood)}</span>${(c.previous_fix_ids || []).length ? ` <span class="chip resolved" title="Confirmed fix in your Fix Log">Previously resolved${c.previous_fix_ids.length > 1 ? " ×" + c.previous_fix_ids.length : ""}</span>` : ""}</div><p>${esc(c.reasoning)}</p></div></div>`).join("")}
    </div>
    <div class="card">
      <div class="card-title">Isolation steps</div>
      <div class="steps">${(r.troubleshooting_steps || []).map((s) => `<div class="step">
        <div class="step-head"><span class="step-no">${s.step}</span><span>${esc(s.action)}</span>
          <span class="dots">${(s.cause_ranks || []).map((k) => `<span class="dot" title="Tests cause ${k}" style="background:${colorFor(k)}"></span>`).join("")}</span></div>
        <dl><dt>Normal</dt><dd>${esc(s.expected)}</dd><dt>If not</dt><dd>${esc(s.if_abnormal)}</dd></dl></div>`).join("")}</div>
    </div>
    <div class="card">
      <div class="card-title">References</div>
      <ul class="refs">${currentRefs.map(refBtn).join("") || "<li class='muted'>No pages cited.</li>"}</ul>
    </div>
    <div class="card">
      <div class="card-title">How this was diagnosed</div>
      <div class="md">${md(r.reasoning)}</div>
    </div>
    ${(r.safety_notes || []).length ? `<div class="card safety"><div class="card-title">Safety</div><ul class="plain">${r.safety_notes.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
    ${(r.missing_info || []).length ? `<div class="card"><div class="card-title">Would help narrow it down</div><ul class="plain">${r.missing_info.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
    <div class="card">
      <div class="card-title">Record the fix</div>
      ${resolution ? `<div class="fixed">✓ Added to the Fix Log: ${esc(resolution)}</div>` : `
      <div class="hint">Once the aircraft is fixed, record what fixed it. It is added to your Fix Log and appears as <b>Previously resolved</b> in future diagnoses.</div>
      <label for="resChecks">What did you find? <span class="muted">(optional)</span></label>
      <textarea id="resChecks" rows="2" placeholder="e.g. 24 V at K45 coil; G202 ground stud loose"></textarea>
      <label for="resolution">What fixed it?</label>
      <textarea id="resolution" rows="2" placeholder="e.g. Cleaned and re-torqued ground stud G202"></textarea>
      <div class="row end"><button id="saveResolution" class="primary">Add to Fix Log</button></div>`}
    </div>`;

  $$("button[data-ref]", el).forEach((b) => b.addEventListener("click", () => {
    selectRef(+b.dataset.ref);
    if (narrow()) diagViewer.setFull(true);
  }));
  const save = $("#saveResolution");
  if (save) save.addEventListener("click", async () => {
    const v = $("#resolution").value.trim();
    if (!v) return $("#resolution").focus();
    await postJSON(`/api/diagnoses/${currentDiagnosisId}/resolution`, { fix: v, checks: $("#resChecks").value.trim() });
    save.closest(".card").innerHTML = `<div class="card-title">Record the fix</div><div class="fixed">✓ Added to the Fix Log: ${esc(v)}</div>`;
    refreshInfo();
  });

  renderRefTabs();
  if (currentRefs.length) selectRef(0);
}

function fixEntry(f, withRelevance) { return `<div class="fix">${fixBody(f, withRelevance)}</div>`; }

function fixBody(f, withRelevance) {
  const meta = [f.aircraft && `Aircraft ${f.aircraft}`, f.system, f.engineer || f.created_by, (f.date || f.created_at || "").slice(0, 10),
    (f.source === "diagnosis") && "recorded after a Vantage diagnosis"].filter(Boolean).map(esc).join(" · ");
  return `
    <div class="fix-line"><span class="k">${f.fix ? "Problem" : "Entry"}</span>${esc(f.symptom || f.title)}${withRelevance && f.match ? `<span class="match ${esc(f.match)}">${f.match === "strong" ? "Strong match" : "Related"}</span>` : ""}</div>
    ${f.checks ? `<div class="fix-line"><span class="k">Found</span>${esc(f.checks)}</div>` : ""}
    ${f.fix ? `<div class="fix-line fix-fixed"><span class="k">Fixed by</span>${esc(f.fix)}</div>`
            : `<div class="fix-line"><span class="k">Notes</span>${esc(f.body || "")}</div>`}
    ${meta ? `<div class="fix-meta">${meta}</div>` : ""}
    ${withRelevance && f.relevance ? `<div class="fix-why">${esc(f.relevance)}</div>` : ""}`;
}

function resolvedBox(fixes) {
  return `<div class="resolved-box">
    <div class="resolved-head"><svg><use href="#i-resolved"/></svg><span class="label">Previously resolved</span></div>
    <div class="resolved-sub">From your Fix Log: your engineers have fixed ${fixes.length === 1 ? "a similar problem" : fixes.length + " similar problems"} before.</div>
    ${fixes.map((f) => fixEntry(f, true)).join("")}
  </div>`;
}

function renderRefTabs() {
  const tabs = $("#refTabs");
  $("#viewerEmpty").classList.toggle("hidden", currentRefs.length > 0);
  tabs.innerHTML = currentRefs.map((r, i) => `<button class="ref-tab" data-i="${i}">${esc(r.ata || r.document)} ${r.printed_page ? "p." + esc(r.printed_page) : ""}<small>${esc((r.subject || "").slice(0, 30))}</small></button>`).join("");
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
    ...r.regions.map((g, k) => ({ key: `r${k}`, label: g.note.length > 28 ? g.note.slice(0, 26) + "…" : g.note || "Area", note: g.note, rank: g.cause_rank, found: true })),
  ];
  $("#refLegend").innerHTML = items.map((it) => `<span class="item ${it.found ? "" : "missing"}" data-key="${it.key}" title="${esc(it.note)}${it.found ? "" : " (not found in the scanned text)"}"><span class="sw" style="background:${colorFor(it.rank)}"></span>${esc(it.label)}</span>`).join("");
  $$("#refLegend .item:not(.missing)").forEach((x) => x.addEventListener("click", () => diagViewer.focus(x.dataset.key)));
}

// ===================================================================== library
let docsCache = [];
let pollTimer = null;

function chosenFiles() { return [...$("#file").files, ...$("#photo").files]; }
["#file", "#photo"].forEach((id) => $(id).addEventListener("change", () => {
  const f = chosenFiles();
  $("#fileChosen").textContent = f.length ? `Selected: ${f.map((x) => x.name).join(", ")}` : "PDF manuals and wiring diagrams, photos or scans, and .txt / .md notes.";
}));

$("#uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = chosenFiles();
  const msg = $("#uploadMsg");
  msg.className = "msg";
  if (!files.length) { msg.textContent = "Choose a file or take a photo first."; return; }
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
  $("#fileChosen").textContent = "PDF manuals and wiring diagrams, photos or scans, and .txt / .md notes.";
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
      <div class="actions">${d.status === "ready" ? `<button class="small" data-browse="${d.id}">Open</button>${d.enrich_status !== "running" && d.enrich_status !== "done" ? `<button class="small" data-enrich="${d.id}" title="Have the AI read every page to improve search">AI-read</button>` : ""}` : ""}<button class="small danger" data-del="${d.id}" title="Delete">Delete</button></div></div>`;
  }).join("") : `<div class="muted">No documents yet. Add your manuals, wiring diagrams and notes above.</div>`;

  $$("[data-browse]", list).forEach((b) => b.addEventListener("click", () => { openBrowse(+b.dataset.browse, 1); if (narrow()) libViewer.setFull(true); }));
  $$("[data-enrich]", list).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Have the AI read every page of this document? This improves search on scanned pages and uses API credits (one request per page).")) return;
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
  if (!$("#tab-library").classList.contains("active")) showTab("library");
  $("#browseDoc").value = docId;
  browsePages = await api(`/api/documents/${docId}/pages`);
  $("#browsePage").innerHTML = browsePages.map((p) => `<option value="${p.page_no}">${p.page_no}. ${esc([p.ata, p.page_label ? "p." + p.page_label : "", p.subject].filter(Boolean).join(" "))}</option>`).join("");
  await showBrowsePage(pageNo, highlights);
}

async function showBrowsePage(pageNo, highlights = []) {
  const docId = +$("#browseDoc").value;
  $("#browsePage").value = pageNo;
  const p = await api(`/api/pages/${docId}/${pageNo}`);
  $("#browseMeta").textContent = [`File page ${p.page_no} of ${p.page_count}`, p.ata, p.page_label && `printed page ${p.page_label}`, p.subject, p.effectivity].filter(Boolean).join(" · ") + (p.ai_summary ? `. ${p.ai_summary}` : "");
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
  const found = await postJSON(`/api/pages/${cur.docId}/${cur.pageNo}/locate`, { terms });
  const hl = terms.map((t, i) => ({ term: t, note: `${found[t].length} found`, cause_rank: (i % 5) + 1, boxes: found[t] }));
  const p = await api(`/api/pages/${cur.docId}/${cur.pageNo}`);
  await libViewer.show(cur.docId, cur.pageNo, hl, [], p.has_image ? null : p.text);
  $("#browseMeta").textContent = terms.map((t) => `${t}: ${found[t].length} found`).join(" · ");
  const first = hl.findIndex((x) => x.boxes.length);
  if (first >= 0) libViewer.focus(`h${first}`);
});

$("#libSearchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#libSearch").value.trim();
  if (!q) return;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
  const out = $("#libResults");
  const pages = res.pages.map((p) => `<div class="hit" data-doc="${p.doc_id}" data-page="${p.page_no}"><div class="h-title">${esc([p.ata, p.page_label && "p." + p.page_label, p.subject].filter(Boolean).join(" ") || "Page " + p.page_no)}</div><div class="h-snip">${esc(p.doc_title)} · file page ${p.page_no}${p.effectivity ? " · " + esc(p.effectivity) : ""}<br>${esc(p.snippet || "")}</div></div>`).join("");
  const fixes = res.fixes.map((n) => `<div class="hit" data-note="${n.id}"><div class="h-title"><span class="chip resolved">Fix Log</span> ${esc(n.symptom || n.title)}</div><div class="h-snip">${n.fix ? "Fixed by: " + esc(n.fix.slice(0, 200)) : esc((n.body || "").slice(0, 200))}</div></div>`).join("");
  out.innerHTML = fixes + pages || `<div class="muted">No matches.</div>`;
  const terms = q.split(/[\s,]+/).filter((t) => t.length > 1);
  $$(".hit[data-doc]", out).forEach((h) => h.addEventListener("click", async () => {
    await openBrowse(+h.dataset.doc, +h.dataset.page);
    $("#findTerms").value = terms.join(", ");
    if (narrow()) libViewer.setFull(true);
    $("#findForm").requestSubmit();
  }));
  $$(".hit[data-note]", out).forEach((h) => h.addEventListener("click", () => { $("#fixSearch").value = q; showTab("fixes"); }));
});

// ===================================================================== Fix Log
$("#fixForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#fixMsg");
  msg.className = "msg";
  try {
    await postJSON("/api/fixes", { symptom: $("#fixSymptom").value, checks: $("#fixChecks").value, fix: $("#fixFix").value,
      aircraft: $("#fixAircraft").value, system: $("#fixSystem").value, tags: $("#fixTags").value });
  } catch (err) { msg.className = "msg err"; msg.textContent = err.message; return; }
  $("#fixForm").reset();
  msg.textContent = "Saved. Vantage will use it in future diagnoses.";
  refreshFixes(); refreshInfo();
});
$("#fixSearchForm").addEventListener("submit", (e) => { e.preventDefault(); refreshFixes(); });
$("#fixSearch").addEventListener("search", () => refreshFixes());

let scopeLoaded = false;
async function loadScopeOptions() {
  if (scopeLoaded || $(".scope-row").classList.contains("hidden")) return;
  scopeLoaded = true;
  const orgs = await api("/api/admin/orgs");
  $("#fixScope").insertAdjacentHTML("beforeend", orgs.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join(""));
}
$("#fixScope").addEventListener("change", () => refreshFixes());

async function refreshFixes() {
  await loadScopeOptions();
  const q = $("#fixSearch").value.trim();
  const scope = $(".scope-row").classList.contains("hidden") ? "own" : $("#fixScope").value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (scope !== "own" && scope !== "all") params.set("org_id", scope);
  const base = scope === "own" ? "/api/fixes" : "/api/admin/fixes";
  const fixes = await api(`${base}${params.toString() ? "?" + params : ""}`);
  const readOnly = scope !== "own";
  $("#fixCount").textContent = q ? `· ${fixes.length} matching` : `· ${fixes.length}`;
  $("#fixList").innerHTML = fixes.length ? fixes.map((f) => `<div class="fix"><div class="fix-actions"><div>${readOnly ? `<span class="org-tag">${esc(f.org_name)}</span>` : ""}${fixBody(f, false)}</div>${readOnly ? "" : `<button class="small danger" data-del="${f.id}">Delete</button>`}</div></div>`).join("")
    : `<div class="muted">${q ? "No matching entries." : "No entries yet. Add the problems your engineers have solved, and Vantage will use them in every diagnosis."}</div>`;
  $$("#fixList [data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this Fix Log entry?")) return;
    await api(`/api/fixes/${b.dataset.del}`, { method: "DELETE" });
    refreshFixes(); refreshInfo();
  }));
}

// ===================================================================== organisation & team
$("#orgForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#orgMsg");
  try {
    await api("/api/org", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("#orgName").value, aircraft_type: $("#orgAircraft").value }) });
    msg.className = "msg"; msg.textContent = "Saved.";
    refreshInfo();
  } catch (err) { msg.className = "msg err"; msg.textContent = err.message; }
});

$("#memberForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#memberMsg");
  try {
    await postJSON("/api/org/members", { name: $("#memName").value, email: $("#memEmail").value,
      password: $("#memPassword").value, is_admin: $("#memAdmin").checked });
    msg.className = "msg"; msg.textContent = `Added. Give them their email and temporary password to sign in.`;
    $("#memberForm").reset();
    refreshOrg();
  } catch (err) { msg.className = "msg err"; msg.textContent = err.message; }
});

$("#newOrgForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#newOrgMsg");
  try {
    await postJSON("/api/admin/orgs", { name: $("#noName").value, aircraft_type: $("#noAircraft").value,
      admin_name: $("#noAdminName").value, admin_email: $("#noAdminEmail").value, admin_password: $("#noAdminPw").value });
    msg.className = "msg"; msg.textContent = "Organisation created. Send the admin their sign-in details.";
    $("#newOrgForm").reset();
    refreshOrg();
  } catch (err) { msg.className = "msg err"; msg.textContent = err.message; }
});

async function refreshOrg() {
  const org = await api("/api/org");
  $("#orgName").value = org.name;
  $("#orgAircraft").value = org.aircraft_type;
  if (org.is_admin) {
    $("#memberList").innerHTML = org.members.map((m) => `<div class="member"><div><b>${esc(m.name || m.email)}</b>${m.is_admin ? '<span class="badge">Admin</span>' : ""}<div class="meta">${esc(m.email)}</div></div>
      ${m.id === org.you ? '<span class="muted">You</span>' : `<button class="small danger" data-rm="${m.id}">Remove</button>`}</div>`).join("") || `<div class="muted">No team members yet.</div>`;
    $$("#memberList [data-rm]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Remove this person from your organisation? They will no longer be able to sign in.")) return;
      await api(`/api/org/members/${b.dataset.rm}`, { method: "DELETE" });
      refreshOrg();
    }));
  }
  if (!$(".platform-only").classList.contains("hidden")) {
    const orgs = await api("/api/admin/orgs");
    $("#orgAdminList").innerHTML = orgs.map((o) => `<div class="org-row"><div><b>${esc(o.name)}</b><span class="badge ${esc(o.status)}">${esc(o.status)}</span>
      <div class="meta">${esc(o.aircraft_type || "No aircraft type")} · ${o.users} users · ${o.documents} documents · ${o.diagnoses} diagnoses</div></div>
      <div class="actions">${o.status === "suspended" ? `<button class="small" data-st="active" data-org="${o.id}">Reactivate</button>` : `<button class="small danger" data-st="suspended" data-org="${o.id}">Suspend</button>`}</div></div>`).join("");
    $$("#orgAdminList [data-st]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.st === "suspended" && !confirm("Suspend this organisation? Its users will lose access until it is reactivated.")) return;
      await api(`/api/admin/orgs/${b.dataset.org}/status`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: b.dataset.st }) });
      refreshOrg();
    }));
  }
}

// ===================================================================== history
async function refreshHistory() {
  const rows = await api("/api/diagnoses");
  $("#historyList").innerHTML = rows.length ? rows.map((r) => `<div class="hist" data-id="${r.id}"><div class="h-title">${esc(r.question.slice(0, 160))}</div><div class="h-meta">${esc(r.created_at)}${r.created_by ? " · " + esc(r.created_by) : ""}${r.serial ? " · S/N " + esc(r.serial) : ""}${r.system ? " · " + esc(r.system) : ""}</div>${r.previous_fixes ? ` · <span class="chip resolved">Previously resolved ×${r.previous_fixes}</span>` : ""}${r.resolution ? `<div class="fixed">✓ Fixed: ${esc(r.resolution)}</div>` : ""}</div>`).join("")
    : `<div class="muted">No diagnoses yet.</div>`;
  $$("#historyList .hist").forEach((h) => h.addEventListener("click", async () => {
    const d = await api(`/api/diagnoses/${h.dataset.id}`);
    showTab("diagnose");
    sessionId = null;
    $("#question").value = d.question.replace(/^\(follow-up\) /, "");
    $("#serial").value = d.serial;
    $("#progress").classList.remove("hidden");
    $("#newDiag").classList.remove("hidden");
    $("#spinner").classList.add("stopped");
    $("#progressTitle").textContent = `Saved diagnosis from ${d.created_at}`;
    $("#trace").innerHTML = "";
    d.trace.forEach(addTrace);
    $("#followForm").classList.add("hidden");
    renderDiagnosis(d.result, d.id, d.resolution);
    $("#diagLeft").scrollTop = 0;
  }));
}

// ===================================================================== start
refreshInfo().then((info) => {
  if (!info.api_key_configured) {
    $("#keyNotice").innerHTML = `<div class="notice">Diagnosis needs an Anthropic API key, and none is set on the server yet. The library, search and Fix Log work without one.</div>`;
  }
  if (!info.documents) showTab("library");
}).catch(() => {});
