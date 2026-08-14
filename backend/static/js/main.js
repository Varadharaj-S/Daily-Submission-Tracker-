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
──────────────────────────────────────────────────────────────────── */

function _importLog(lines, status) {
  if (!status) return;
  status.style.display = "block";
  status.className = "sync-status";
  status.innerHTML = lines.map(l => `<div>${l}</div>`).join("");
}

async function importLC() {
  if (window.importRunning) return;
  window.importRunning = true;

  const btn     = document.getElementById("importLcBtn");
  const label   = document.getElementById("importLcLabel");
  const spinner = document.getElementById("importLcSpinner");
  const status  = document.getElementById("importStatus");
  if (!btn) { window.importRunning = false; return; }

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
    let res = await fetch(BASE_API_URL + "/import_codeforces", { method: "POST", credentials: "include" });
    let data = await res.json();
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
    res = await fetch(BASE_API_URL + "/import_atcoder", { method: "POST", credentials: "include" });
    data = await res.json();
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
      res = await fetch(BASE_API_URL + "/import_leetcode", { method: "POST", credentials: "include" });
      data = await res.json();
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
  } catch (err) {
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