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
    const res = await fetch("/sync", { method: "POST" });
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

async function importLeetCode() {
  if (window.importRunning) return;
  window.importRunning = true;

  const btn     = document.getElementById("importLcBtn");
  const label   = document.getElementById("importLcLabel");
  const spinner = document.getElementById("importLcSpinner");
  const status  = document.getElementById("syncStatus");

  if (!btn) {
    window.importRunning = false;
    return;
  }

  if (!confirm("This will import your full LeetCode history, update Google Sheet, and then disable this button. Continue?")) {
    window.importRunning = false;
    return;
  }

  btn.disabled = true;
  if (label) label.style.display = "none";
  if (spinner) spinner.style.display = "inline-block";
  if (status) {
    status.style.display = "block";
    status.className = "sync-status";
    status.textContent = "Importing full history...";
  }

  try {
    const res = await fetch("/import_lc", { method: "POST" });
    const data = await res.json();
    if (status) {
      status.className = "sync-status " + (data.success ? "sync-ok" : "sync-err");
      status.textContent = data.message || (data.success ? "Import started" : "Import failed");
    }
    showToast(data.message || (data.success ? "Import started ✅" : "Import failed"), data.success ? "success" : "error");
    if (data.success) {
      setTimeout(() => location.reload(), 5000);
    } else {
      btn.disabled = false;
    }
  } catch {
    showToast("Network error — please try again.", "error");
    btn.disabled = false;
  } finally {
    if (label) label.style.display = "inline";
    if (spinner) spinner.style.display = "none";
    window.importRunning = false;
  }
}

async function toggleAutoSync() {
  const toggle = document.getElementById("autoSyncToggle");
  if (!toggle) return;
  try {
    const res = await fetch("/toggle_auto_sync", {
      method: "POST",
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
    const res = await fetch("/set_sync_time", {
      method: "POST",
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
    const res = await fetch("/sync", { method: "POST" });
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
  const res = await fetch(`/challenge/complete/${id}`, { method: "POST" });
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
  const res = await fetch(`/mentor/complete/${id}`, { method: "POST" });
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
