"""
app.py — DSA Tracker v4 entry point.

This used to be a single 2,400+ line file containing every route, every
helper, the DB schema, the User model, and the scheduler. It's now just
the assembly point:

  1. `extensions` creates the shared Flask `app` (config, login manager,
     CORS, before/after_request hooks).
  2. `database.db.init_db()` creates tables if they don't exist yet — this
     runs unconditionally at import time, same as the original app.py did
     (so it also runs correctly under gunicorn, not just `python app.py`).
  3. Each `routes.*` module is imported for its side effect of registering
     `@app.route(...)` handlers onto the shared `app` object. No Flask
     Blueprints are used, specifically so every endpoint name (and every
     `url_for(...)` call, in Python and in the Jinja templates) stays
     byte-for-byte identical to the original monolith.
  4. Error handlers and the dev-server bootstrap live here, same as they
     did at the bottom of the old app.py.
"""

import os

from extensions import app
from database.db import init_db, ensure_extension_schema, ensure_year_schema, ensure_recommendation_schema, ensure_db_columns
from services.tracker_service import ensure_tracker_schema
from contest.contest_service import ensure_contest_schema

# ── DB (PostgreSQL only — see database/db.py) ────────────────────────────────
init_db()

# extension_token isn't part of init_db()'s baseline CREATE TABLE, so on an
# already-existing users table it has to be patched in separately. Runs
# unconditionally (like ensure_contest_schema() below) so it also happens on
# Vercel cold starts, not just under `python app.py`'s __main__ block.
ensure_extension_schema()

# PHASE 2 — users.cohort_year + year_sheets mapping table. Same
# unconditional-on-cold-start reasoning as ensure_extension_schema().
ensure_year_schema()

# contest_events/contest_results/contest_problems are NOT part of init_db()'s
# baseline schema — they were added later via database/migrate.py, a
# standalone script nobody runs against the Vercel production DB automatically.
# That's why /student_contest was 500ing: the table just doesn't exist there.
# Calling this here (unconditionally, like init_db() above) means it now runs
# on every cold start, including under wsgi.py/gunicorn on Vercel.
ensure_contest_schema()

# PHASE 3 — recommendations table (mentor Recommendation + News system,
# year-isolated via cohort_year). Same unconditional-on-cold-start
# reasoning as ensure_year_schema() above.
ensure_recommendation_schema()

# PHASE 4 FIX: ensure_db_columns() (full_name/reg_no/roll_no/branch/
# lc_imported/auto_sync_enabled/sync_time) and ensure_tracker_schema() were
# only being called inside the `if __name__ == "__main__":` block below —
# meaning gunicorn/Vercel production cold starts (which import this module,
# they don't run it as __main__) never got these columns/tables patched in.
# That's the exact same class of bug the comment above ensure_contest_schema()
# already describes and fixed for the contest tables; these two were missed.
# Moved here, unconditional, same reasoning as every other ensure_*() call
# on this page.
ensure_db_columns()
ensure_tracker_schema()

# ── Route modules (import for side-effect route registration) ───────────────
from routes import auth            # noqa: E402,F401  /, /login, /signup, /logout, /admin/login, email verification
from routes import dashboard       # noqa: E402,F401  /dashboard, challenge/mentor completion
from routes import settings        # noqa: E402,F401  /settings, leetcode connect, feedback
from routes import ext_pairing     # noqa: E402,F401  /save_cookie, /extension/* — Chrome extension pairing (Bearer token). File is named ext_pairing.py, NOT extension.py — that name is one letter from extensions.py (the shared Flask app module) and Vercel's Python builder was mixing the two up during bundling.
from routes import sync            # noqa: E402,F401  /sync, /import_lc
from routes import tracker         # noqa: E402,F401  /problems, /export_csv, daily tracker
from routes import google_sheet    # noqa: E402,F401  /my_sheet
from routes import leaderboard     # noqa: E402,F401  /user/<username>, /follow, /friends, /leaderboard
from routes import admin           # noqa: E402,F401  /admin/*
from routes import reports         # noqa: E402,F401  /weekly_report, /weekly_csv, /api/weekly_report
from routes import contest        # noqa: E402,F401  /student_contest, /contest/* (Contest Tracker, Phase 1)
from routes import recommendations # noqa: E402,F401  /recommendations, /admin/recommendations/* (Phase 3 — mentor Recommendation + News system)
from routes import analytics       # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import notifications   # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import api             # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import cron            # noqa: E402,F401  /api/cron/daily-sync (Vercel Cron entry point, PART 3)
from routes import session_api     # noqa: E402,F401  /api/auth/me (frontend/backend split, PART 5)
from routes import internal        # noqa: E402,F401  /internal/* — Render-only endpoints for delegated long-running work


# ── Error handlers ────────────────────────────────────────────────────────────
from flask import jsonify


@app.errorhandler(404)
def e404(e):
    return jsonify({"success": False, "code": 404, "message": "Page not found."}), 404


@app.errorhandler(403)
def e403(e):
    return jsonify({"success": False, "code": 403, "message": "Access denied."}), 403


@app.errorhandler(429)
def e429(e):
    return jsonify({"success": False, "code": 429, "message": "Too many requests."}), 429


# ── Background auto-sync thread ──────────────────────────────────────────────
# BUG FIX: this used to live inside `if __name__ == "__main__":` below, which
# only runs for `python app.py` (local dev server). Under gunicorn (Render's
# Procfile: `gunicorn app:app`) or Vercel, this module is only ever
# *imported* (via wsgi.py / vercel.json) — `__main__` never executes there —
# so start_sync_worker() never ran in production and the in-process
# auto-sync safety net silently did nothing, even though the "Auto Sync"
# toggle in Settings looked like it was on. Same bug class as
# ensure_db_columns()/ensure_tracker_schema() above (see the comment there);
# fixed the same way: call it unconditionally at import time so it also
# fires on gunicorn workers, not just the dev server.
#
# NOTE for local `python app.py` with the Werkzeug reloader (debug=True):
# the reloader re-imports this module in a child process, so the thread may
# start twice locally. Harmless for this safety-net loop (same as the
# schema-init calls above already running unconditionally on every cold
# start) — it just means two redundant sync sweeps in dev, never in
# production where there's no reloader.
from workers.sync_worker import start_sync_worker
start_sync_worker()


if __name__ == "__main__":
    app.run(debug=True)
