"""
routes/sync.py — manual "sync now" trigger and first-time LeetCode full
import.

KEY FIX (Vercel 504):
Previously /import_lc called run_background(_do_import, user_id) which on
Vercel ran the full bot_sheet_sync.py subprocess SYNCHRONOUSLY inside the
request (because run_background() detects the VERCEL env var and falls back
to in-process). That subprocess hammers LC's GraphQL API hundreds of times
with 0.35 s sleeps between requests, easily taking 5–15 minutes for a large
account — way past Vercel's ~10 s serverless timeout.

FLOW:
  POST /import_lc          (Vercel)
    → validate session
    → POST to the persistent worker's /internal/trigger_import
      (fire-and-forget via requests, short timeout — we only wait for the ACK)
    → return {"success": true, "status": "started"} immediately

  GET /import_lc/status    (Vercel)
    → GET worker's /internal/import_status/<user_id>
    → forward result to frontend (for poll loop)

The actual import still runs in bot_sheet_sync.py — nothing about the
import logic itself changes. It just moves from "inside the Vercel request"
to "background thread on the persistent worker backend" (Render — see
render.yaml / Procfile).

CONFIG (single source of truth: config.py's Config class):
  WORKER_BACKEND_URL  — base URL of the persistent worker (Render), e.g.
                         https://dsa-tracker.onrender.com
  INTERNAL_TASK_SECRET — shared secret for the X-Internal-Secret header,
                         must match on both the Vercel and Render deployments.

Whenever BOTH are configured we delegate to the worker (this is what makes
production on Vercel work without a 504). When either is missing — i.e. this
process *is* the persistent worker (Render) or we're running locally — we
run the import in-process via a background thread, same as before this
handoff existed.
"""

import os

import requests as _requests
from flask import jsonify, request
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from services.sync_engine import sync_user_data
from config import Config


# ── helpers ───────────────────────────────────────────────────────────────────

def _worker_url(path: str) -> str:
    """Build a URL pointing at the persistent worker backend (Config.WORKER_BACKEND_URL,
    no trailing slash). Only called when that value is already known to be set."""
    return f"{Config.WORKER_BACKEND_URL.rstrip('/')}{path}"


def _internal_headers() -> dict:
    return {"X-Internal-Secret": Config.INTERNAL_TASK_SECRET, "Content-Type": "application/json"}


def _worker_configured() -> bool:
    return bool(Config.WORKER_BACKEND_URL and Config.INTERNAL_TASK_SECRET)


# ── /sync (manual incremental sync) ──────────────────────────────────────────

@app.route("/sync", methods=["POST"])
@login_required
@verified_required
def sync():
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=%s", (current_user.id,)
        ).fetchone()

    user = dict(user)

    # block if not imported
    if int(user.get("lc_imported", 0)) == 0:
        return jsonify({
            "success": False,
            "message": "❌ Please import LeetCode first"
        })

    result = sync_user_data(user, get_db)
    return jsonify(result)


# ── /import_lc  ───────────────────────────────────────────────────────────────

@app.route("/import_lc", methods=["POST"])
@login_required
def import_lc():
    """
    Triggers a full LeetCode import WITHOUT keeping the Vercel request open.

    When the worker is configured (Config.WORKER_BACKEND_URL + Config.INTERNAL_TASK_SECRET
    both set): POSTs to the worker's /internal/trigger_import and returns
    immediately with {"success": true, "status": "started"}.

    Otherwise (this process IS the persistent worker — Render — or local dev):
    falls back to running the import in a background thread in-process.
    """
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=%s", (current_user.id,)
        ).fetchone()

    if not user or not user["lc_session_cookie"]:
        return jsonify({"success": False, "message": "Connect LeetCode first"})

    user_id = current_user.id

    # PERF/safety: don't let a double-click (or a retried request) start a
    # second concurrent import for the same user — that would double every
    # LeetCode GraphQL call this process makes, race the DB insert, and
    # race the Google Sheet rebuild. lc_import_status lives in the shared
    # Postgres DB (same DB for both the Vercel layer and the Render worker),
    # so this check is accurate regardless of which one handles the actual
    # import. This only blocks a *duplicate* import for the SAME user; it
    # never touches other users, and status polling below remains strictly
    # read-only (never restarts anything).
    user_dict = dict(user)
    if str(user_dict.get("lc_import_status") or "").lower() in ("queued", "running"):
        return jsonify({
            "success": True,
            "status": "started",
            "message": "Import already in progress. You can continue using the dashboard."
        })

    if _worker_configured():
        # ── Delegate to the persistent worker, return immediately ────────
        try:
            resp = _requests.post(
                _worker_url("/internal/trigger_import"),
                json={"user_id": user_id},
                headers=_internal_headers(),
                timeout=10          # we only wait for the ACK, not the import
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[import_lc] could not reach worker backend: {e}")
            return jsonify({
                "success": False,
                "message": (
                    "Could not reach the import worker. "
                    "Make sure WORKER_BACKEND_URL and INTERNAL_TASK_SECRET "
                    "are set correctly (and match) on both deployments."
                )
            }), 503

        return jsonify({
            "success": True,
            "status": "started",
            "message": (
                "LeetCode import started. "
                "You can continue using the dashboard — "
                "your sheet will update in a few minutes."
            )
        })

    else:
        # ── This process is the worker (or local dev): run in-process ────
        # (run_background uses a daemon thread here — the process stays alive)
        from utils.helpers import run_background

        def _do_import(uid):
            import subprocess, sys, os
            subprocess.run(
                [sys.executable, "bot_sheet_sync.py", str(uid), "import"],
                capture_output=False,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )

        run_background(_do_import, user_id)

        return jsonify({
            "success": True,
            "status": "started",
            "message": "Import started. Your sheet will be updated shortly."
        })


# ── /import_lc/status  ────────────────────────────────────────────────────────

@app.route("/import_lc/status", methods=["GET"])
@login_required
def import_lc_status():
    """
    Returns the current import status for the logged-in user.
    Frontend polls this endpoint after pressing Import LC.

    When the worker is configured: proxies the request to the worker's
    /internal/import_status/<user_id>. Otherwise reads lc_import_status
    directly from the DB (this process IS the worker, or local dev).

    Possible statuses: queued | running | completed | failed | unknown
    """
    user_id = current_user.id

    if _worker_configured():
        # ── Ask the worker ─────────────────────────────────────────────────
        try:
            resp = _requests.get(
                _worker_url(f"/internal/import_status/{user_id}"),
                headers=_internal_headers(),
                timeout=8
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return jsonify({"success": False, "status": "unknown", "error": str(e)})

        return jsonify({
            "success": True,
            "status": data.get("status", "unknown"),
            "lc_imported": data.get("lc_imported", 0)
        })

    else:
        # ── This process is the worker (or local dev): read from DB ───────
        with get_db() as db:
            row = db.execute(
                "SELECT lc_import_status, lc_imported FROM users WHERE id=%s",
                (user_id,)
            ).fetchone()

        if not row:
            return jsonify({"success": False, "status": "unknown"})

        return jsonify({
            "success": True,
            "status": row["lc_import_status"] or "unknown",
            "lc_imported": row["lc_imported"]
        })
