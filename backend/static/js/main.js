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

/* ── Import LC — ONE button, THREE real sequential backend requests ────
   importLC() is the entire workflow behind the single "Import LC"
   button. It performs, IN ORDER, awaiting each real HTTP response
   before starting the next:

     1) POST /import_codeforces   (one request, real result)
     2) POST /import_atcoder      (one request, real result)
     3) POST /import_leetcode     (one request per chunk; looped here
        while the real response says has_more:true, using the offset
        the backend already persisted — never restarted at 0)

   This is NOT status polling: every call in the loop is a genuine
   import request that processes the next data chunk and returns a
   real result (fetched/written counts), never a bare {"status":
   "started"}. There is no setInterval and no /status endpoint.

   If Codeforces or AtCoder fails, the whole workflow stops immediately
   and the real error is shown — AtCoder/LeetCode never start. If a
   LeetCode chunk fails, the loop stops at that chunk; the backend
   never advanced lc_import_offset for the failed chunk, so pressing
   Import LC again retries safely from the same position (after CF/AC
   re-run, since CF/AtCoder/LeetCode always run in that fixed order).

   NOTE: this function used to be written into backend/static/js/main.js
   (a file dashboard.html never actually <script>-loads) instead of
   here. That was the root cause of Import LC doing nothing — this is
   the same implementation, moved into the file that's actually served. */

function _importLog(lines, status) {
  if (!status) return;
  status.style.display = "block";
  status.className = "sync-status";
  status.innerHTML = lines.map(l => `<div>${l}</div>`).join("");
}

async function importLC() {
  const btn = document.getElementById("importLcBtn");
  if (!btn) {
    console.error("[IMPORT LC] BUTTON NOT FOUND");
    return;
  }
  console.log("[IMPORT LC] BUTTON FOUND");
  console.log("[IMPORT LC] CLICKED");

  if (window.importRunning) return;
  window.importRunning = true;

  const label   = document.getElementById("importLcLabel");
  const spinner = document.getElementById("importLcSpinner");
  const status  = document.getElementById("importStatus");

  if (!confirm("This imports your Codeforces, AtCoder, and LeetCode history (in that order) and updates your sheet. Continue?")) {
    window.importRunning = false;
    return;
  }

  btn.disabled = true;
  if (spinner) spinner.style.display = "inline-block";

  const lines = ["Importing..."];
  _importLog(lines, status);

  const tStart = performance.now();
  const totals = { cfFetched: 0, cfNew: 0, acFetched: 0, acNew: 0, lcFetched: 0, lcNew: 0 };

  const setLast = (text) => { lines[lines.length - 1] = text; };

  try {
    // CALL 1/3 — Codeforces (one request, real result)
    lines.push("Codeforces → Running...");
    _importLog(lines, status);
    console.log("[IMPORT LC] SENDING REQUEST", "/import_codeforces");
    let res = await fetch(BASE_API_URL + "/import_codeforces", { method: "POST", credentials: "include" });
    console.log("[IMPORT LC] REQUEST SENT");
    let data = await res.json();
    console.log("[IMPORT LC] RESPONSE RECEIVED", res.status, data);
    if (!data.success) {
      setLast(`Codeforces → Failed — ${data.message || "unknown error"}`);
      _importLog(lines, status);
      showToast(data.message || "Codeforces import failed", "error");
      return;
    }
    totals.cfFetched = data.rows_fetched || 0;
    totals.cfNew = data.db_rows_written || 0;
    setLast("Codeforces → Completed");
    _importLog(lines, status);

    // CALL 2/3 — AtCoder (one request, real result)
    lines.push("AtCoder → Running...");
    _importLog(lines, status);
    console.log("[IMPORT LC] SENDING REQUEST", "/import_atcoder");
    res = await fetch(BASE_API_URL + "/import_atcoder", { method: "POST", credentials: "include" });
    console.log("[IMPORT LC] REQUEST SENT");
    data = await res.json();
    console.log("[IMPORT LC] RESPONSE RECEIVED", res.status, data);
    if (!data.success) {
      setLast(`AtCoder → Failed — ${data.message || "unknown error"}`);
      _importLog(lines, status);
      showToast(data.message || "AtCoder import failed", "error");
      return;
    }
    totals.acFetched = data.rows_fetched || 0;
    totals.acNew = data.db_rows_written || 0;
    setLast("AtCoder → Completed");
    _importLog(lines, status);

    // CALL 3/3 — LeetCode, one real request per chunk, auto-continued
    // here (not polling — each iteration is a fresh import request that
    // processes the next saved offset) until has_more is false.
    let chunkNum = 1;
    let hasMore = true;
    while (hasMore) {
      lines.push(`LeetCode → Chunk ${chunkNum}...`);
      _importLog(lines, status);
      console.log("[IMPORT LC] SENDING REQUEST", "/import_leetcode", `chunk ${chunkNum}`);
      res = await fetch(BASE_API_URL + "/import_leetcode", { method: "POST", credentials: "include" });
      console.log("[IMPORT LC] REQUEST SENT");
      data = await res.json();
      console.log("[IMPORT LC] RESPONSE RECEIVED", res.status, data);
      if (!data.success) {
        setLast(`LeetCode → Chunk ${chunkNum} failed — ${data.message || "unknown error"}`);
        _importLog(lines, status);
        showToast(data.message || "LeetCode import failed", "error");
        return;
      }
      totals.lcFetched += data.rows_fetched || 0;
      totals.lcNew += data.db_rows_written || 0;
      hasMore = !!data.has_more;
      setLast(`LeetCode → Chunk ${chunkNum} (${data.rows_fetched || 0} fetched)`);
      _importLog(lines, status);
      chunkNum++;
    }

    const totalSeconds = ((performance.now() - tStart) / 1000).toFixed(1);
    lines.push("Import Completed ✅");
    lines.push(
      `CF ${totals.cfFetched} fetched / ${totals.cfNew} new · ` +
      `AC ${totals.acFetched} fetched / ${totals.acNew} new · ` +
      `LC ${totals.lcFetched} fetched / ${totals.lcNew} new · ${totalSeconds}s total`
    );
    _importLog(lines, status);
    showToast("Import complete ✅", "success");
    setTimeout(() => location.reload(), 2500);
  } catch (error) {
    console.error("[IMPORT LC] CLICK HANDLER ERROR", error);
    lines.push("Import failed — network error, please try again");
    _importLog(lines, status);
    showToast("Network error — please try again.", "error");
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = "none";
    window.importRunning = false;
  }
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