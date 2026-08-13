"""
routes/cron.py — Vercel Cron entry points for daily auto-sync and contest sync.

ARCHITECTURE:
  Render is the authoritative scheduler. render.yaml defines two cron
  services that call `python scheduler.py` and `python contest_scheduler.py`
  on a real persistent process — those run fine because Render keeps the
  process alive.

  Vercel Cron Jobs work by making an HTTP GET/POST to a route in the
  deployed serverless app on a schedule. But that route runs inside a
  Vercel serverless function, which has a hard timeout (~10 s on Hobby,
  ~60 s on Pro) — not nearly enough time to sync many students.

  CORRECT BEHAVIOR ON VERCEL:
  These routes do NOT run the sync inline. Instead they POST to the Render
  persistent backend's /internal/trigger_import or the daily-sync endpoint,
  and return immediately.  The actual work runs on Render.

  CORRECT BEHAVIOR ON RENDER (local dev, gunicorn):
  The route is never called by Vercel cron — Render runs the cron services
  directly. But if it IS called (manual curl, etc.) it runs inline as before.

Security: CRON_SECRET guards against external callers.
          INTERNAL_SECRET is used for the Render-side internal endpoints.
"""

import os
import threading

import requests as _requests
from flask import request, jsonify

from extensions import app


def _check_cron_secret():
    secret = os.environ.get("CRON_SECRET", "")
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            return False
    return True


def _render_url(path: str) -> str:
    base = os.environ.get("APP_URL", "http://localhost:5000").rstrip("/")
    return f"{base}{path}"


def _internal_headers() -> dict:
    return {
        "X-Internal-Secret": os.environ.get("INTERNAL_SECRET", ""),
        "Content-Type": "application/json",
    }


@app.route("/api/cron/daily-sync", methods=["GET", "POST"])
def cron_daily_sync():
    """
    Called by Vercel Cron (vercel.json schedule).

    On Vercel:  POSTs to Render /internal/cron/daily-sync → returns fast.
    On Render:  Runs scheduler.main() inline (Render's own cron calls
                python scheduler.py directly so this branch is rarely hit).
    """
    if not _check_cron_secret():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    if os.environ.get("VERCEL"):
        # Delegate to Render — don't run the full sync here.
        target = _render_url("/internal/cron/daily-sync")
        secret = os.environ.get("INTERNAL_SECRET", "")
        if not secret:
            return jsonify({
                "ok": False,
                "message": "INTERNAL_SECRET not set — cannot delegate to Render backend."
            }), 503
        try:
            resp = _requests.post(target, headers=_internal_headers(), timeout=10)
            resp.raise_for_status()
        except Exception as e:
            return jsonify({"ok": False, "message": f"Render delegation failed: {e}"}), 503
        return jsonify({"ok": True, "message": "Daily sync delegated to Render backend"})

    # Render / local: run inline
    from scheduler import main as run_daily_sync
    run_daily_sync()
    return jsonify({"ok": True, "message": "Daily sync completed"})


@app.route("/api/cron/contest-sync", methods=["GET", "POST"])
def cron_contest_sync():
    """
    Called by Vercel Cron (vercel.json schedule).
    Same delegation pattern as cron_daily_sync().
    """
    if not _check_cron_secret():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    if os.environ.get("VERCEL"):
        target = _render_url("/internal/cron/contest-sync")
        secret = os.environ.get("INTERNAL_SECRET", "")
        if not secret:
            return jsonify({
                "ok": False,
                "message": "INTERNAL_SECRET not set — cannot delegate to Render backend."
            }), 503
        try:
            resp = _requests.post(target, headers=_internal_headers(), timeout=10)
            resp.raise_for_status()
        except Exception as e:
            return jsonify({"ok": False, "message": f"Render delegation failed: {e}"}), 503
        return jsonify({"ok": True, "message": "Contest sync delegated to Render backend"})

    # Render / local: run inline
    from contest_scheduler import main as run_contest_sync
    run_contest_sync()
    return jsonify({"ok": True, "message": "Contest sync completed"})
