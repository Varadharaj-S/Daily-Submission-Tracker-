"""
routes/sync.py — manual "sync now" trigger and first-time LeetCode full
import. Moved verbatim from app.py.
"""

import os
import sys
import subprocess
import threading

import requests
from flask import jsonify, request
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from utils.helpers import run_background
from services.sync_engine import sync_user_data
from config import Config


# ── Sync ──────────────────────────────────────────────────────────────────────
@app.route("/sync", methods=["POST"])
@login_required
@verified_required
def sync():
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=?", (current_user.id,)
        ).fetchone()

    user = dict(user)

    # block if not imported
    if int(user.get("lc_imported", 0)) == 0:
        return jsonify({
            "success": False,
            "message": "❌ Please import LeetCode first"
        })

    # Daily sync should not re-fetch LeetCode.
    # Full import is handled by /import_lc and bot_sheet_sync.py.

    result = sync_user_data(user, get_db)
    return jsonify(result)


def _do_import(user_id):
    print("=" * 50)
    print("🚀 _do_import STARTED")
    print("USER ID =", user_id)
    print("=" * 50)

    result = subprocess.run(
        [sys.executable, "bot_sheet_sync.py", str(user_id), "import"],
        capture_output=False
    )

    print("RETURN CODE =", result.returncode)


def _trigger_remote_import(user_id):
    """
    Vercel-only handoff (see /import_lc below). The actual import
    (bot_sheet_sync.py, which resolves the year sheet via cohort_year ->
    year_sheet_service) can run for minutes — well past Vercel's
    serverless request timeout, which is what produced the 504s. Vercel
    has no persistent process to run it in, but this same codebase is
    also deployed to Render as an always-on gunicorn service (render.yaml
    / Procfile) — that's the "persistent backend/worker" this hands off
    to.

    We POST to a small internal-only route on that Render deployment
    (protected by INTERNAL_TASK_SECRET, not the user's login session,
    since this is a server-to-server call) with a short timeout, so this
    Vercel request returns immediately. By the time the short timeout
    fires, Render has already accepted the request and started the
    import in its own background thread; being a persistent process, it
    keeps running to completion regardless of whether we're still
    waiting on the HTTP response.

    Returns True if the handoff request was sent, False if
    WORKER_BACKEND_URL / INTERNAL_TASK_SECRET aren't configured.
    """
    backend_url = Config.WORKER_BACKEND_URL
    secret = Config.INTERNAL_TASK_SECRET

    if not backend_url or not secret:
        print("⚠️  WORKER_BACKEND_URL / INTERNAL_TASK_SECRET not set — "
              "cannot hand off /import_lc to the persistent backend.")
        return False

    try:
        requests.post(
            f"{backend_url.rstrip('/')}/internal/run_import_lc",
            json={"user_id": user_id},
            headers={"X-Internal-Secret": secret},
            timeout=3,
        )
    except requests.exceptions.RequestException:
        # Expected in the common case: we intentionally don't wait for the
        # import to finish. Render has already received and started the
        # job; a timeout/connection-closed exception here just means this
        # short-lived kickoff call ended, not that the import failed.
        pass

    return True


# ── LeetCode Full Import (First Time — uses LEETCODE_SESSION cookie) ─────────
@app.route("/import_lc", methods=["POST"])
@login_required
def import_lc():
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=?",
            (current_user.id,)
        ).fetchone()

    if not user["lc_session_cookie"]:
        return jsonify({
            "success": False,
            "message": "Connect LeetCode first"
        })

    if os.environ.get("VERCEL"):
        # Run on the persistent Render backend instead of inside this
        # serverless request — see _trigger_remote_import().
        started = _trigger_remote_import(current_user.id)
        if not started:
            return jsonify({
                "success": False,
                "message": "Import worker isn't configured yet. Please contact an admin."
            }), 503
    else:
        # Unchanged: Render/local already run this in a background thread
        # and return immediately (run_background() only runs synchronously
        # when VERCEL is set, which it isn't here).
        run_background(_do_import, current_user.id)

    return jsonify({
        "success": True,
        "message": "Import started ✅ Your sheet will be updated in a few minutes."
    })


@app.route("/internal/run_import_lc", methods=["POST"])
def internal_run_import_lc():
    """
    Internal-only endpoint: the persistent backend's side of the handoff
    from /import_lc above. Deliberately NOT @login_required — this is a
    server-to-server call from the Vercel deployment, not a browser
    request with a user session, so it's authenticated by
    INTERNAL_TASK_SECRET instead.

    Only meaningful when running off-Vercel (Render/local), where a
    daemon thread actually survives to finish the import — same pattern
    run_background() already uses.
    """
    secret = request.headers.get("X-Internal-Secret", "")
    expected = Config.INTERNAL_TASK_SECRET
    if not expected or secret != expected:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    if not user_id:
        return jsonify({"success": False, "message": "user_id required"}), 400

    threading.Thread(target=_do_import, args=(user_id,), daemon=True).start()
    return jsonify({"success": True})