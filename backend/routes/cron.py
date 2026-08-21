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
  ~60 s on Pro) — not nearly enough time to sync many students in one
  shot if the sync ran unbounded.

  CORRECT BEHAVIOR ON VERCEL:
  If WORKER_BACKEND_URL is set, these routes delegate to that persistent
  Render backend's /internal endpoints and return immediately — the
  actual work runs there. If it's NOT set (the Cloudflare+Vercel-only
  deployment this repo currently ships as), cron_daily_sync() instead
  loops scheduler.sync_batch() itself inline, batch by batch, until
  either every due user is covered or it's about to run out of this
  invocation's time budget (see CRON_TIME_BUDGET_SECONDS) — this is what
  makes a single Vercel Cron hit (registered in vercel.json) actually
  cover the active roster instead of only the first batch.
  frontend/worker.js's Cloudflare Cron Trigger can also drive the same
  endpoint externally with its own longer-running loop (Workers aren't
  time-boxed the way Vercel functions are) — both are safe to run
  together, since is_due_for_auto_sync() already skips anyone already
  synced today.

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
    Cloudflare Cron Trigger loop (see that file's scheduled() handler).

    Delegates to a persistent worker if WORKER_BACKEND_URL is configured
    (unused in the Cloudflare+Vercel-only setup, kept for anyone who does
    add a Render worker later). Otherwise loops scheduler.sync_batch()
    itself, batch after batch, until either every due user has been
    covered or CRON_TIME_BUDGET_SECONDS is about to run out — NOT just one
    15-user batch and done.

    Why loop here instead of relying only on an external caller: Vercel
    Cron previously wasn't registered for this route at all (vercel.json
    only listed contest-sync), so nothing was ever calling this
    automatically without the separate Cloudflare Worker also being
    deployed and correctly configured — from the Vercel side alone,
    auto-sync looked like it only ever ran when someone manually hit it
    once. Now that vercel.json does register this route, and on the
    Hobby plan Vercel only allows an entry to fire once a day, a single
    invocation looping internally is what makes that one daily hit
    actually cover the whole active roster (time budget permitting)
    instead of just the first 15 users by id every single day forever.
    Safe to run alongside the Cloudflare Worker's own external loop too —
    both call the same idempotent sync_batch(), and is_due_for_auto_sync()
    already skips anyone synced today, so double-coverage just no-ops.

    Pass ?after_id=<cursor> to start partway through (mainly for the
    Cloudflare Worker's own external loop); response includes
    "next_cursor" and "done" either way.
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

    # No worker configured: loop bounded batches inline until either every
    # due user is covered or we're about to run out of this invocation's
    # execution budget. Defaults to 8s — safely under Vercel Hobby's ~10s
    # hard cap; raise CRON_TIME_BUDGET_SECONDS (e.g. to ~50) on Pro, where
    # the cap is ~60s and this route is also scheduled every 15 minutes.
    from scheduler import sync_batch
    import time as _time

    try:
        after_id = int(request.args.get("after_id", 0))
    except (TypeError, ValueError):
        after_id = 0

    time_budget = float(os.environ.get("CRON_TIME_BUDGET_SECONDS", "8"))
    started = _time.monotonic()

    totals = {"processed": 0, "scanned": 0, "ok": 0, "fail": 0, "cookie_expired": 0}
    batches = 0
    done = False

    while _time.monotonic() - started < time_budget:
        result = sync_batch(after_id=after_id)
        batches += 1
        for k in ("processed", "scanned", "ok", "fail", "cookie_expired"):
            totals[k] += result.get(k, 0)
        after_id = result["next_cursor"]
        done = result["done"]
        if done:
            break

    return jsonify({
        "ok": True,
        "batches": batches,
        "next_cursor": after_id,
        "done": done,
        **totals,
    })


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
