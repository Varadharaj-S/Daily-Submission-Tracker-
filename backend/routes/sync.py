"""
routes/sync.py — manual "sync now" trigger and first-time LeetCode full
import. Moved verbatim from app.py.
"""

import sys
import subprocess

from flask import jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from utils.helpers import run_background
from services.sync_engine import sync_user_data


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

    run_background(_do_import, current_user.id)

    return jsonify({
        "success": True,
        "message": "Import started"
    })
