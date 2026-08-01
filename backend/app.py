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
from database.db import init_db
from services.tracker_service import ensure_tracker_schema

# ── DB (PostgreSQL only — see database/db.py) ────────────────────────────────
init_db()

# ── Route modules (import for side-effect route registration) ───────────────
from routes import auth            # noqa: E402,F401  /, /login, /signup, /logout, /admin/login, email verification
from routes import dashboard       # noqa: E402,F401  /dashboard, challenge/mentor completion
from routes import settings        # noqa: E402,F401  /settings, cookie endpoints, leetcode connect, feedback
from routes import sync            # noqa: E402,F401  /sync, /import_lc
from routes import tracker         # noqa: E402,F401  /problems, /export_csv, daily tracker
from routes import google_sheet    # noqa: E402,F401  /my_sheet
from routes import leaderboard     # noqa: E402,F401  /user/<username>, /follow, /friends, /leaderboard
from routes import admin           # noqa: E402,F401  /admin/*
from routes import reports         # noqa: E402,F401  /weekly_report, /weekly_csv, /api/weekly_report
from backend.routes import contest_route         # noqa: E402,F401  /student_contest, /contest/* (Contest Tracker, Phase 1)
from routes import analytics       # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import notifications   # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import api             # noqa: E402,F401  (Phase 3 — currently empty, see file docstring)
from routes import cron            # noqa: E402,F401  /api/cron/daily-sync (Vercel Cron entry point, PART 3)
from routes import session_api     # noqa: E402,F401  /api/auth/me (frontend/backend split, PART 5)


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


if __name__ == "__main__":
    from database.db import ensure_db_columns
    from workers.sync_worker import start_sync_worker

    ensure_db_columns()
    ensure_tracker_schema()

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_sync_worker()

    app.run(debug=True)
