"""
routes/internal.py — Internal endpoints only reachable from within the
persistent worker deployment (Render — see render.yaml / Procfile), or via
a shared secret. NOT exposed to the public.

These endpoints allow the Vercel serverless layer to delegate long-running
work (like a full LeetCode import) to the Render persistent backend, so the
Vercel request can return immediately and avoid a 504.

Security: every request must carry the correct Config.INTERNAL_TASK_SECRET
value in the X-Internal-Secret header. Set INTERNAL_TASK_SECRET to the same
long random string in both your Vercel and Render environment variable
panels (see config.py — this is the single source of truth for the name).
"""

import os
import threading

from flask import request, jsonify

from extensions import app
from database.db import get_db
from config import Config


def _require_internal_secret():
    """Returns (True, None) if the request is authorized, (False, response) otherwise."""
    secret = Config.INTERNAL_TASK_SECRET
    if not secret:
        # No secret configured → deny all; don't let this endpoint be open.
        return False, (jsonify({"ok": False, "error": "INTERNAL_TASK_SECRET not configured"}), 403)

    provided = request.headers.get("X-Internal-Secret", "")
    if provided != secret:
        return False, (jsonify({"ok": False, "error": "Forbidden"}), 403)

    return True, None


def _run_import(user_id: int):
    """Runs the full LC import in a background thread (safe on Render — the
    process stays alive between requests). Updates lc_import_status in the
    users table so the frontend can poll for completion."""
    import subprocess, sys

    # Mark as running
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET lc_import_status=%s WHERE id=%s",
                ("running", user_id)
            )
            db.commit()
    except Exception as e:
        print(f"[internal] could not mark running: {e}")

    try:
        result = subprocess.run(
            [sys.executable, "bot_sheet_sync.py", str(user_id), "import"],
            capture_output=False,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        status = "completed" if result.returncode == 0 else "failed"
        print(f"[internal] import finished for user {user_id}, status={status}")
    except Exception as e:
        status = "failed"
        print(f"[internal] import exception for user {user_id}: {e}")

    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET lc_import_status=%s WHERE id=%s",
                (status, user_id)
            )
            db.commit()
    except Exception as e:
        print(f"[internal] could not mark {status}: {e}")


@app.route("/internal/trigger_import", methods=["POST"])
def internal_trigger_import():
    """Called by the Vercel /import_lc route.
    Validates the secret, then starts the import in a background thread
    and returns immediately so the caller never waits for the full import."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "user_id required"}), 400

    # Verify user exists and has a LeetCode session saved
    with get_db() as db:
        user = db.execute(
            "SELECT id, lc_session_cookie FROM users WHERE id=%s", (user_id,)
        ).fetchone()

    if not user:
        return jsonify({"ok": False, "error": "user not found"}), 404
    if not user["lc_session_cookie"]:
        return jsonify({"ok": False, "error": "no lc_session_cookie for user"}), 400

    # Mark queued immediately
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET lc_import_status=%s WHERE id=%s",
                ("queued", user_id)
            )
            db.commit()
    except Exception as e:
        print(f"[internal] could not mark queued: {e}")

    # Fire the import in a background thread — safe on Render (persistent process)
    t = threading.Thread(target=_run_import, args=(user_id,), daemon=True)
    t.start()

    return jsonify({"ok": True, "status": "queued", "user_id": user_id})


@app.route("/internal/import_status/<int:user_id>", methods=["GET"])
def internal_import_status(user_id):
    """Returns the current lc_import_status for a user.
    Called by the Vercel /import_lc/status proxy endpoint (and by the
    frontend's poll loop via that proxy)."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    with get_db() as db:
        row = db.execute(
            "SELECT lc_import_status, lc_imported FROM users WHERE id=%s",
            (user_id,)
        ).fetchone()

    if not row:
        return jsonify({"ok": False, "error": "user not found"}), 404

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "status": row["lc_import_status"] or "unknown",
        "lc_imported": row["lc_imported"]
    })


# ── Cron delegation endpoints ─────────────────────────────────────────────────
# Vercel Cron routes POST here so the actual long-running work executes on
# the Render persistent backend, not inside a Vercel serverless function.

@app.route("/internal/cron/daily-sync", methods=["POST"])
def internal_cron_daily_sync():
    """Triggered by Vercel cron → routes/cron.py → here. Runs scheduler.main()
    in a background thread so this endpoint returns fast enough for the caller."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    def _run():
        try:
            from scheduler import main as run_daily_sync
            run_daily_sync()
        except Exception as e:
            print(f"[internal cron daily-sync] error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Daily sync started in background"})


@app.route("/internal/cron/contest-sync", methods=["POST"])
def internal_cron_contest_sync():
    """Triggered by Vercel cron → routes/cron.py → here."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    def _run():
        try:
            from contest_scheduler import main as run_contest_sync
            run_contest_sync()
        except Exception as e:
            print(f"[internal cron contest-sync] error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Contest sync started in background"})
