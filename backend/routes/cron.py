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
          INTERNAL_TASK_SECRET (config.py) is used for the worker-side
          internal endpoints (routes/internal.py).
"""

import os
import threading

import requests as _requests
from flask import request, jsonify

from extensions import app
from config import Config


def _check_cron_secret():
    secret = os.environ.get("CRON_SECRET", "")
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            return False
    return True


def _worker_url(path: str) -> str:
    return f"{Config.WORKER_BACKEND_URL.rstrip('/')}{path}"


def _internal_headers() -> dict:
    return {
        "X-Internal-Secret": Config.INTERNAL_TASK_SECRET,
        "Content-Type": "application/json",
    }


def _worker_configured() -> bool:
    return bool(Config.WORKER_BACKEND_URL and Config.INTERNAL_TASK_SECRET)


@app.route("/api/cron/daily-sync", methods=["GET", "POST"])
def cron_daily_sync():
    """
    Called by Vercel Cron (vercel.json schedule) OR by frontend/worker.js's
    Cloudflare Cron Trigger loop (this deployment's actual driver — see
    that file's scheduled() handler; no Render worker exists here).

    Delegates to a persistent worker if WORKER_BACKEND_URL is configured
    (unused in the Cloudflare+Vercel-only setup, kept for anyone who does
    add a Render worker later). Otherwise runs exactly ONE batch of users
    (scheduler.sync_batch) and returns — it does NOT try to sync everyone
    in a single request, because a Vercel function only gets ~10s (Hobby) /
    60s (Pro), nowhere near enough for hundreds/thousands of users.
    Pass ?after_id=<cursor> to resume; response includes "next_cursor" and
    "done" so the caller knows whether to call again.
    """
    if not _check_cron_secret():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    if _worker_configured():
        # Delegate to the persistent worker — don't run the full sync here.
        try:
            resp = _requests.post(_worker_url("/internal/cron/daily-sync"), headers=_internal_headers(), timeout=10)
            resp.raise_for_status()
        except Exception as e:
            return jsonify({"ok": False, "message": f"Worker delegation failed: {e}"}), 503
        return jsonify({"ok": True, "message": "Daily sync delegated to worker backend"})

    # No worker configured: run one bounded batch inline and return. The
    # caller (Cloudflare Cron Trigger, see frontend/worker.js) loops this
    # with the returned next_cursor until done=true.
    from scheduler import sync_batch

    try:
        after_id = int(request.args.get("after_id", 0))
    except (TypeError, ValueError):
        after_id = 0

    result = sync_batch(after_id=after_id)
    return jsonify({"ok": True, **result})


@app.route("/api/cron/contest-sync", methods=["GET", "POST"])
def cron_contest_sync():
    """
    Called by Vercel Cron (vercel.json schedule).
    Same delegation pattern as cron_daily_sync().
    """
    if not _check_cron_secret():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401

    if _worker_configured():
        try:
            resp = _requests.post(_worker_url("/internal/cron/contest-sync"), headers=_internal_headers(), timeout=10)
            resp.raise_for_status()
        except Exception as e:
            return jsonify({"ok": False, "message": f"Worker delegation failed: {e}"}), 503
        return jsonify({"ok": True, "message": "Contest sync delegated to worker backend"})

    # This process is the worker (or local dev): run inline
    from contest_scheduler import main as run_contest_sync
    run_contest_sync()
    return jsonify({"ok": True, "message": "Contest sync completed"})
