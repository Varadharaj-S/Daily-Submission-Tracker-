/* DSA Tracker v3 — main.js */

function showToast(msg, type = "success") {
  const c = document.getElementById("flashContainer");
  if (!c) return;
  const t = document.createElement("div");
  t.className = `toast toast-${type}`;
  t.innerHTML = `<span class="toast-icon">${type==="success"?"✓":type==="warning"?"⚠":"✕"}</span>
    <span class="toast-msg">${msg}</span>
    <button class="toast-close" onclick="this.parentElement.remove()">×</button>`;
  c.appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity .4s, transform .4s";
    t.style.opacity = "0";
    t.style.transform = "translateX(110%)";
    setTimeout(() => t.remove(), 400);
  }, 4500);
}

document.querySelectorAll(".toast").forEach((t, i) => {
  setTimeout(() => {
    t.style.transition = "opacity .4s, transform .4s";
    t.style.opacity = "0";
    t.style.transform = "translateX(110%)";
    setTimeout(() => t.remove(), 400);
  }, 4000 + i * 600);
});

async function triggerSync() {
  const btn     = document.getElementById("syncBtn");
  const label   = document.getElementById("syncLabel");
  const spinner = document.getElementById("syncSpinner");
  const status  = document.getElementById("syncStatus");
  if (!btn) return;

  btn.disabled = true;
  if (label) label.style.display = "none";
  if (spinner) spinner.style.display = "inline-block";
  if (status) status.style.display = "none";

  try {
    const res = await fetch(BASE_API_URL + "/sync", { method: "POST", credentials: "include" });
    if (res.status === 429) {
      showToast("Rate limit — please wait a moment.", "error");
      return;
    }
    const data = await res.json();
    if (status) {
      status.style.display = "block";
      status.className = "sync-status " + (data.success ? "sync-ok" : "sync-err");
      status.textContent = data.message;
    }
    showToast(data.message || (data.success ? "Sync completed ✅" : "Sync failed"), data.success ? "success" : "error");
    if (data.success) setTimeout(() => location.reload(), 1400);
  } catch {
    showToast("Network error — please try again.", "error");
  } finally {
    btn.disabled = false;
    if (label) label.style.display = "inline";
    if (spinner) spinner.style.display = "none";
  }
}

/* ── Import — three independent single-request actions ─────────────────
   Each function below sends exactly ONE POST to its own endpoint and
   awaits the real final JSON result. No polling, no setInterval, no
   "started" response, no automatic follow-up request. For LeetCode,
   has_more:true just means the button stays enabled so the user can
   press it again for the next chunk — nothing here calls it again on
   its own. ──────────────────────────────────────────────────────── */

async function _runImport({ endpoint, btnId, labelId, spinnerId, defaultLabel, confirmMsg, reloadOnDone }) {
  if (window.importRunning) return;
  window.importRunning = true;

  const btn     = document.getElementById(btnId);
  const label   = document.getElementById(labelId);
  const spinner = document.getElementById(spinnerId);
  const status  = document.getElementById("importStatus");

  if (!btn) { window.importRunning = false; return; }

  if (confirmMsg && !confirm(confirmMsg)) {
    window.importRunning = false;
    return;
  }

  btn.disabled = true;
  if (spinner) spinner.style.display = "inline-block";
  if (status) {
    status.style.display = "block";
    status.className = "sync-status";
    status.textContent = "Working...";
  }

  try {
    const res = await fetch(BASE_API_URL + endpoint, { method: "POST", credentials: "include" });
    const data = await res.json();

    if (status) {
      status.className = "sync-status " + (data.success ? "sync-ok" : "sync-err");
      status.textContent = data.message || (data.success ? "Done ✅" : "Failed");
    }
    showToast(data.message || (data.success ? "Done ✅" : "Failed"), data.success ? "success" : "error");

    if (data.success && reloadOnDone && !data.has_more) {
      setTimeout(() => location.reload(), 3000);
    } else {
      btn.disabled = false;
      // For a partial LeetCode chunk, relabel the button so it's clear
      // pressing it again continues from where it left off — the user
      // must press it; nothing here does that automatically.
      if (label && data.has_more) label.textContent = defaultLabel + " (continue)";
    }
  } catch {
    showToast("Network error — please try again.", "error");
    btn.disabled = false;
  } finally {
    if (spinner) spinner.style.display = "none";
    window.importRunning = false;
  }
}

/* ── Import LC — ONE button, orchestrated as multiple short-lived
   requests ───────────────────────────────────────────────────────────
   importLC() is the single function the "Import LC" button calls. It
   drives, in order:
     POST /import_codeforces  (one request, real completion)
     POST /import_atcoder     (one request, real completion)
     POST /import_leetcode    (one or more chunked requests, looped here
                               in the browser until completed:true)
   Each request is independent and short-lived (backend targets ~120s
   per request) — nothing here holds one HTTP connection open for the
   whole import, and nothing polls a /status endpoint. The popup only
   ever reflects an ACTUAL response from one of the three endpoints
   above; nothing is simulated. Stops immediately (and leaves the
   Import LC button available) the moment any request fails.
   initial_import_completed itself is computed and persisted by the
   backend (see routes/sync.py) from cf_imported/ac_imported/lc_imported
   — this function just reads that field back off each response so a
   refresh, logout/login, or second device always agrees with it. ── */

function _importLCEnsureStyles() {
  if (document.getElementById("importLCStyles")) return;
  const s = document.createElement("style");
  s.id = "importLCStyles";
  s.textContent = `
    #importLCOverlay{position:fixed;inset:0;background:rgba(6,8,15,.72);
      display:flex;align-items:center;justify-content:center;z-index:9999;
      backdrop-filter:blur(2px);}
    #importLCBox{width:min(420px,92vw);background:var(--bg2,#12141c);
      border:1px solid var(--border,#2a2d3a);border-radius:16px;
      padding:1.4rem 1.5rem;box-shadow:0 20px 60px rgba(0,0,0,.5);
      font-family:var(--mono,monospace);}
    #importLCBox h3{margin:0 0 .9rem;font-size:1.05rem;font-weight:800;
      letter-spacing:-.02em;color:var(--text,#f1f5f9);}
    .ilc-row{display:flex;align-items:flex-start;gap:.6rem;padding:.5rem 0;
      border-bottom:1px solid var(--border2,rgba(255,255,255,.06));}
    .ilc-row:last-child{border-bottom:none;}
    .ilc-icon{font-size:1.05rem;line-height:1.4;width:1.4rem;flex:none;text-align:center;}
    .ilc-body{flex:1;min-width:0;}
    .ilc-title{font-weight:700;font-size:.9rem;color:var(--text,#f1f5f9);}
    .ilc-sub{font-size:.78rem;color:var(--muted,#94a3b8);margin-top:.15rem;line-height:1.4;}
    #importLCFooter{margin-top:1.1rem;display:flex;gap:.6rem;justify-content:flex-end;}
    #importLCBox .btn{font-size:.82rem;padding:.5rem .9rem;}
    .ilc-spin{display:inline-block;width:12px;height:12px;border-radius:50%;
      border:2px solid rgba(148,163,184,.35);border-top-color:var(--accent,#22d3ee);
      animation:ilc-spin .7s linear infinite;vertical-align:middle;}
    @keyframes ilc-spin{to{transform:rotate(360deg);}}
  `;
  document.head.appendChild(s);
}

function _importLCRender(state) {
  const stepMeta = {
    codeforces: "Codeforces",
    atcoder: "AtCoder",
    leetcode: "LeetCode",
  };
  const rowHtml = (key) => {
    const st = state.steps[key];
    let icon = "⏳", sub = "Waiting...";
    if (st.status === "running") { icon = `<span class="ilc-spin"></span>`; sub = st.sub || "Running..."; }
    else if (st.status === "done") { icon = "✅"; sub = st.sub || "Completed"; }
    else if (st.status === "error") { icon = "❌"; sub = st.sub || "Failed"; }
    return `<div class="ilc-row">
      <div class="ilc-icon">${icon}</div>
      <div class="ilc-body">
        <div class="ilc-title">${stepMeta[key]}</div>
        <div class="ilc-sub">${sub}</div>
      </div>
    </div>`;
  };

  let footer = `<span style="font-size:.78rem;color:var(--muted,#94a3b8);align-self:center;margin-right:auto;">${state.footerNote || ""}</span>`;
  if (state.finished) {
    footer += `<button class="btn btn-outline" onclick="document.getElementById('importLCOverlay').remove()">Close</button>`;
  } else if (state.failed) {
    footer += `<button class="btn btn-outline" onclick="document.getElementById('importLCOverlay').remove()">Close</button>
                <button class="btn btn-primary" onclick="document.getElementById('importLCOverlay').remove();importLC();">Retry</button>`;
  }

  const box = document.getElementById("importLCBox");
  if (!box) return;
  box.innerHTML = `
    <h3>${state.finished ? "🎉 IMPORT LC" : "IMPORT LC"}</h3>
    ${rowHtml("codeforces")}${rowHtml("atcoder")}${rowHtml("leetcode")}
    <div id="importLCFooter">${footer}</div>
  `;
}

function _importLCOpen() {
  _importLCEnsureStyles();
  let overlay = document.getElementById("importLCOverlay");
  if (overlay) overlay.remove();
  overlay = document.createElement("div");
  overlay.id = "importLCOverlay";
  overlay.innerHTML = `<div id="importLCBox"></div>`;
  document.body.appendChild(overlay);
}

async function importLC() {
  if (window.importRunning) return;
  window.importRunning = true;

  const btn = document.getElementById("importLcBtn");
  if (btn) btn.disabled = true;

  const state = {
    steps: {
      codeforces: { status: "running", sub: "Running..." },
      atcoder: { status: "waiting", sub: "Waiting..." },
      leetcode: { status: "waiting", sub: "Waiting..." },
    },
    finished: false,
    failed: false,
    footerNote: "",
  };
  _importLCOpen();
  _importLCRender(state);

  async function postJSON(endpoint) {
    const res = await fetch(BASE_API_URL + endpoint, { method: "POST", credentials: "include" });
    let data;
    try { data = await res.json(); } catch { data = {}; }
    return data;
  }

  function fail(stepKey, message) {
    state.steps[stepKey].status = "error";
    state.steps[stepKey].sub = message || "Failed";
    state.failed = true;
    state.footerNote = "Import stopped — nothing further was run.";
    _importLCRender(state);
    showToast(message || `${stepKey} import failed`, "error");
    if (btn) btn.disabled = false;
    window.importRunning = false;
  }

  // ── Codeforces ──────────────────────────────────────────────────────
  let cfData;
  try {
    cfData = await postJSON("/import_codeforces");
  } catch {
    return fail("codeforces", "Network error contacting Codeforces import.");
  }
  if (!cfData || !cfData.success) return fail("codeforces", cfData?.message || "Codeforces import failed.");
  state.steps.codeforces = { status: "done", sub: `Written rows: ${cfData.db_rows_written ?? cfData.rows_fetched ?? 0}` };
  state.steps.atcoder = { status: "running", sub: "Running..." };
  _importLCRender(state);

  // ── AtCoder ─────────────────────────────────────────────────────────
  let acData;
  try {
    acData = await postJSON("/import_atcoder");
  } catch {
    return fail("atcoder", "Network error contacting AtCoder import.");
  }
  if (!acData || !acData.success) return fail("atcoder", acData?.message || "AtCoder import failed.");
  state.steps.atcoder = { status: "done", sub: `Written rows: ${acData.db_rows_written ?? acData.rows_fetched ?? 0}` };
  state.steps.leetcode = { status: "running", sub: "Running chunk 1..." };
  _importLCRender(state);

  // ── LeetCode — resumable chunks, looped here until completed:true ───
  let chunk = 1;
  let lcTotalRows = 0;
  let lcData;
  while (true) {
    try {
      lcData = await postJSON("/import_leetcode");
    } catch {
      return fail("leetcode", "Network error contacting LeetCode import.");
    }
    if (!lcData || !lcData.success) return fail("leetcode", lcData?.message || "LeetCode import failed.");

    lcTotalRows += (lcData.db_rows_written ?? 0);

    if (lcData.has_more) {
      chunk += 1;
      state.steps.leetcode = { status: "running", sub: `LeetCode chunk ${chunk - 1} completed ✓ — running chunk ${chunk}...` };
      _importLCRender(state);
      continue;
    }

    // Full history done.
    state.steps.leetcode = { status: "done", sub: `Written rows: ${lcTotalRows}` };
    state.finished = true;
    state.footerNote = lcData.initial_import_completed
      ? "Initial import completed successfully."
      : "LeetCode finished, but the overall import isn't marked complete yet.";
    _importLCRender(state);
    break;
  }

  if (lcData.initial_import_completed) {
    showToast("🎉 Initial import completed successfully.", "success");
    setTimeout(() => location.reload(), 1800);
  } else {
    showToast("Import finished with an unexpected state — refresh to check.", "warning");
    btn && (btn.disabled = false);
  }
  window.importRunning = false;
}

async function importCodeforces() {
  return _runImport({
    endpoint: "/import_codeforces",
    btnId: "importCfBtn", labelId: "importCfLabel", spinnerId: "importCfSpinner",
    defaultLabel: "📥 Import Codeforces",
    reloadOnDone: true,
  });
}

async function importAtCoder() {
  return _runImport({
    endpoint: "/import_atcoder",
    btnId: "importAcBtn", labelId: "importAcLabel", spinnerId: "importAcSpinner",
    defaultLabel: "📥 Import AtCoder",
    reloadOnDone: true,
  });
}

async function importLeetCodeChunk() {
  return _runImport({
    endpoint: "/import_leetcode",
    btnId: "importLcBtn", labelId: "importLcLabel", spinnerId: "importLcSpinner",
    defaultLabel: "📥 Import LeetCode",
    confirmMsg: "This imports one chunk of your LeetCode history (continuing from where you left off) and updates your sheet. Press it again afterwards if more history remains. Continue?",
    reloadOnDone: true,
  });
}

async function toggleAutoSync() {
  const toggle = document.getElementById("autoSyncToggle");
  if (!toggle) return;
  try {
    const res = await fetch(BASE_API_URL + "/toggle_auto_sync", {
      method: "POST",
      credentials: "include",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ enabled: toggle.checked })
    });
    const data = await res.json();
    showToast(data.enabled ? "Auto sync enabled ✅" : "Auto sync disabled", "success");
  } catch {
    toggle.checked = !toggle.checked;
    showToast("Could not update auto sync setting.", "error");
  }
}

async function saveSyncTime() {
  const timeInput = document.getElementById("syncTime");
  if (!timeInput) return;
  try {
    const res = await fetch(BASE_API_URL + "/set_sync_time", {
      method: "POST",
      credentials: "include",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ time: timeInput.value })
    });
    const data = await res.json();
    showToast(`Auto sync time saved: ${data.sync_time || timeInput.value}`, "success");
  } catch {
    showToast("Could not save sync time.", "error");
  }
}

async function runSync() {
    const res = await fetch(BASE_API_URL + "/sync", { method: "POST", credentials: "include" });
    const data = await res.json();

    document.getElementById("new-count").innerText =
        "🆕 New Problems: " + data.new_count;
}

function animateCounter(el, target, duration = 850) {
  const start = performance.now();
  const step = (now) => {
    const p = Math.min((now - start) / duration, 1);
    el.textContent = Math.round((1 - Math.pow(1 - p, 3)) * target);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

document.querySelectorAll(".stat-num[data-target]").forEach(el => {
  const val = parseInt(el.dataset.target, 10);
  if (!isNaN(val) && val > 0) {
    el.textContent = "0";
    setTimeout(() => animateCounter(el, val), 120);
  }
});

function togglePwd(id, btn) {
  const inp = document.getElementById(id);
  if (!inp) return;
  inp.type = inp.type === "password" ? "text" : "password";
  btn.textContent = inp.type === "password" ? "👁" : "🙈";
}

async function completeChallenge(id) {
  const res = await fetch(`${BASE_API_URL}/challenge/complete/${id}`, { method: "POST", credentials: "include" });
  const data = await res.json();
  if (data.success) {
    const el = document.getElementById(`ch-${id}`);
    if (el) {
      el.classList.add("challenge-done");
      const btn = el.querySelector(".check-btn");
      if (btn) btn.outerHTML = '<span class="check-done">✓</span>';
    }
    showToast("Challenge marked complete! 🎉", "success");
  }
}

async function completeMentor(id) {
  const res = await fetch(`${BASE_API_URL}/mentor/complete/${id}`, { method: "POST", credentials: "include" });
  const data = await res.json();
  if (data.success) {
    const el = document.getElementById(`mt-${id}`);
    if (el) {
      el.classList.add("challenge-done");
      const btn = el.querySelector(".check-btn");
      if (btn) btn.outerHTML = '<span class="check-done">✓</span>';
    }
    showToast("Mentor task done! 🏆", "success");
  }
}

(function () {
  const cards = document.querySelectorAll(
    ".stat-card, .chart-card, .table-card, .settings-card, .challenge-item, .workflow-card"
  );
  cards.forEach((card, i) => {
    card.style.opacity = "0";
    card.style.transform = "translateY(12px)";
    card.style.transition = `opacity .38s ease ${i * 50}ms, transform .38s ease ${i * 50}ms`;
    setTimeout(() => {
      card.style.opacity = "1";
      card.style.transform = "translateY(0)";
    }, 30);
  });
})();

document.querySelectorAll(".nav-link").forEach(l =>
  l.addEventListener("click", () =>
    document.getElementById("navLinks")?.classList.remove("open")
  )
);

/* ── Generic client-side pagination helpers ─────────────────────────────
   Reused by admin.html / mentor.html so every long list (users, logs,
   student progress, assignments, search results) is paginated the same
   way instead of dumping everything on one page. */
function paginateArray(arr, page, perPage) {
  arr = arr || [];
  const total = arr.length;
  const total_pages = Math.max(1, Math.ceil(total / perPage));
  page = Math.min(Math.max(1, page || 1), total_pages);
  const start = (page - 1) * perPage;
  return { items: arr.slice(start, start + perPage), page, total_pages, total };
}

function paginationHTML(page, total_pages, onClickFn, extraArgs = "") {
  if (total_pages <= 1) return "";
  const args = extraArgs ? extraArgs + "," : "";
  return `<div class="pagination">
    <button class="page-btn" ${page <= 1 ? "disabled style='opacity:.4;cursor:default'" : ""} onclick="${onClickFn}(${args}${page - 1})">← Prev</button>
    <span class="page-info mono">Page ${page} / ${total_pages}</span>
    <button class="page-btn" ${page >= total_pages ? "disabled style='opacity:.4;cursor:default'" : ""} onclick="${onClickFn}(${args}${page + 1})">Next →</button>
  </div>`;
}