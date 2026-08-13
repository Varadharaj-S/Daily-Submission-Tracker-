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

NEW FLOW:
  POST /import_lc          (Vercel)
    → validate session
    → POST to Render /internal/trigger_import   (fire-and-forget via requests)
    → return {"success": true, "status": "started"} immediately

  GET /import_lc/status    (Vercel)
    → GET Render /internal/import_status/<user_id>
    → forward result to frontend (for poll loop)

The actual import still runs in bot_sheet_sync.py — nothing about the
import logic itself changes. It just moves from "inside the Vercel request"
to "background thread on the Render persistent backend".
"""

import os

import requests as _requests
from flask import jsonify, request
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from services.sync_engine import sync_user_data


# ── helpers ───────────────────────────────────────────────────────────────────

def _render_url(path: str) -> str:
    """Build a URL pointing at the Render persistent backend.
    APP_URL must be set to e.g. https://dsa-tracker.onrender.com
    (no trailing slash).  If it's missing we fall back to the local
    Flask server so nothing breaks in local dev."""
    base = os.environ.get("APP_URL", "http://localhost:5000").rstrip("/")
    return f"{base}{path}"


def _internal_secret() -> str:
    return os.environ.get("INTERNAL_SECRET", "")


def _internal_headers() -> dict:
    return {"X-Internal-Secret": _internal_secret(), "Content-Type": "application/json"}


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

    On Vercel:  POSTs to Render's /internal/trigger_import and returns
                immediately with {"success": true, "status": "started"}.

    On Render / local: falls back to the original background-thread behavior
                (run_background still works fine on a persistent process).
    """
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=%s", (current_user.id,)
        ).fetchone()

    if not user or not user["lc_session_cookie"]:
        return jsonify({"success": False, "message": "Connect LeetCode first"})

    user_id = current_user.id
    secret  = _internal_secret()

    if os.environ.get("VERCEL") and secret:
        # ── Vercel path: delegate to Render, return immediately ──────────
        render_trigger = _render_url("/internal/trigger_import")
        try:
            resp = _requests.post(
                render_trigger,
                json={"user_id": user_id},
                headers=_internal_headers(),
                timeout=10          # we only wait for the ACK, not the import
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[import_lc] could not reach Render backend: {e}")
            return jsonify({
                "success": False,
                "message": (
                    "Could not reach the import worker. "
                    "Make sure APP_URL and INTERNAL_SECRET are set correctly."
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
        # ── Render / local path: original fire-and-forget thread ─────────
        # (run_background uses a daemon thread here — the process stays alive)
        from utils.helpers import run_background
        import subprocess, sys

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

    On Vercel: proxies the request to Render's /internal/import_status.
    On Render / local: reads lc_import_status directly from the DB.

    Possible statuses: queued | running | completed | failed | unknown
    """
    user_id = current_user.id
    secret  = _internal_secret()

    if os.environ.get("VERCEL") and secret:
        # ── Vercel path: ask Render ───────────────────────────────────────
        render_status = _render_url(f"/internal/import_status/{user_id}")
        try:
            resp = _requests.get(
                render_status,
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
        # ── Render / local path: read from DB directly ────────────────────
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
