/*
assets/js/app.js — shared across every page (PART 5: frontend/backend
split). Replaces what base.html used to do server-side with Jinja:
  - render the nav bar based on current_user
  - show flash() messages
  - gate pages behind login/admin/verified checks
All requests go to BASE_API_URL (see config.js) with credentials:'include'
so the Flask session cookie travels cross-site to the Vercel backend.
*/

// ── Low-level fetch helpers ────────────────────────────────────────────────
async function apiGet(path) {
  const res = await fetch(BASE_API_URL + path, { credentials: "include" });
  return _handleResponse(res);
}

async function apiPostJSON(path, data) {
  const res = await fetch(BASE_API_URL + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {})
  });
  return _handleResponse(res);
}

// Some backend routes read Flask's request.form (unchanged from the
// original monolith), so their bodies must be sent as
// application/x-www-form-urlencoded, not JSON.
async function apiPostForm(path, data) {
  const params = new URLSearchParams();
  Object.entries(data || {}).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach(item => params.append(k, item));
    else if (v !== undefined && v !== null) params.append(k, v);
  });
  const res = await fetch(BASE_API_URL + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString()
  });
  return _handleResponse(res);
}

async function _handleResponse(res) {
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (res.status === 401) {
    // Session missing/expired — same effect as flask-login's old
    // login_required redirect, just done client-side now.
    window.location.href = "login.html";
    return data;
  }
  data.__status = res.status;
  return data;
}

function qs(name) {
  return new URLSearchParams(window.location.search).get(name);
}

async function openMySheet() {
  const data = await apiGet("/my_sheet");
  if (data.url) window.open(data.url, "_blank");
  else showToast(data.message || "Could not open sheet.", "error");
}

async function openMasterSheet(year) {
  // Admin/Mentor "My Sheet" is year-scoped (PHASE 4) — a year must be
  // explicitly selected (see admin.html's year selector) and gets sent
  // as ?year=, same trusted-year-selection pattern mentor.html already
  // uses. Never silently falls back to a student's cohort_year.
  if (!year) {
    showToast("Select a year first.", "error");
    return;
  }
  const data = await apiGet(`/admin/master_sheet?year=${encodeURIComponent(year)}`);
  if (data.url) window.open(data.url, "_blank");
  else showToast(data.message || "Could not open master sheet.", "error");
}

// ── Auth guard ──────────────────────────────────────────────────────────────
// Call at the top of every protected page. Mirrors @login_required /
// @admin_required / @verified_required from utils/decorators.py.
async function requireAuth({ admin = false, verified = false } = {}) {
  const me = await apiGet("/api/auth/me");
  if (!me.authenticated) {
    window.location.href = "login.html";
    return null;
  }
  if (admin && !me.is_admin) {
    window.location.href = "dashboard.html";
    return null;
  }
  if (verified && (!me.is_verified || me.status !== "active")) {
    window.location.href = "pending.html";
    return null;
  }
  renderNav(me);
  return me;
}

// ── Nav bar (was the {% if current_user.is_authenticated %} block in base.html) ──
function renderNav(me) {
  const mount = document.getElementById("navContainer");
  if (!mount || !me || !me.authenticated) return;

  const path = window.location.pathname.split("/").pop();
  const active = (names) => names.includes(path) ? "active" : "";

  let links;
  if (me.is_admin) {
    links = `
      <a href="admin.html" class="nav-link ${active(["admin.html"])}">👑 Admin</a>
      <a href="mentor.html" class="nav-link ${active(["mentor.html"])}">🎓 Mentor</a>
      <a href="contest_dashboard.html" class="nav-link ${active(["contest_dashboard.html","contest_create.html","contest_history.html"])}">⭐ Student Contest</a>
      <a href="admin_students.html" class="nav-link ${active(["admin_students.html"])}">🧾 Students</a>
      <a href="mentor_recommendations.html" class="nav-link ${active(["mentor_recommendations.html"])}">💡 Recommendations</a>`;
  } else {
    links = `
      <a href="dashboard.html" class="nav-link ${active(["dashboard.html","index.html",""])}">Dashboard</a>
      <a href="problems.html" class="nav-link ${active(["problems.html"])}">Problems</a>
      <a href="recommendations.html" class="nav-link ${active(["recommendations.html"])}">💡 Recommendations</a>
      <a href="weekly_report.html" class="nav-link ${active(["weekly_report.html"])}">📊 Report</a>
      <a href="daily_tracker.html" class="nav-link ${active(["daily_tracker.html"])}">🗂 Daily Tracker</a>
      <a href="leaderboard.html" class="nav-link ${active(["leaderboard.html"])}">Leaderboard</a>
      <a href="friends.html" class="nav-link ${active(["friends.html"])}">Friends</a>`;
  }

  const avatar = !me.is_admin
    ? `<a href="profile.html?username=${encodeURIComponent(me.username)}" class="nav-link"><span class="nav-avatar">${(me.username[0] || "?").toUpperCase()}</span></a>`
    : "";

  mount.innerHTML = `
    <nav class="navbar">
      <a href="index.html" class="nav-brand">⚡ DSA<span>Tracker</span></a>
      <div class="nav-links" id="navLinks">
        ${links}
        <div class="nav-divider"></div>
        ${avatar}
        <a href="settings.html" class="nav-link ${active(["settings.html"])}">Settings</a>
        <a href="#" class="nav-link nav-logout" id="logoutLink">Logout</a>
      </div>
      <button class="nav-toggle" onclick="document.getElementById('navLinks').classList.toggle('open')">☰</button>
    </nav>`;

  document.getElementById("logoutLink").addEventListener("click", async (e) => {
    e.preventDefault();
    const data = await apiGet("/logout");
    window.location.href = data.redirect || "login.html";
  });

  document.querySelectorAll(".nav-link").forEach(l =>
    l.addEventListener("click", () => document.getElementById("navLinks")?.classList.remove("open"))
  );
}

// ── Flash-message replacement ──────────────────────────────────────────────
// showToast(msg, type) itself is defined in assets/js/main.js (moved
// verbatim). This just reads a message handed across a redirect via the
// query string (e.g. the email-verification link) or via sessionStorage
// (e.g. right after a successful login before the page navigates).
function showQueuedMessages() {
  const success = qs("success");
  const error = qs("error");
  if (success) showToast(success, "success");
  if (error) showToast(error, "error");

  const queued = sessionStorage.getItem("flash_message");
  if (queued) {
    sessionStorage.removeItem("flash_message");
    const [type, msg] = queued.split("::", 2);
    showToast(msg, type);
  }
}

function queueFlash(type, msg) {
  sessionStorage.setItem("flash_message", `${type}::${msg}`);
}

document.addEventListener("DOMContentLoaded", showQueuedMessages);
