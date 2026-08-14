"""
routes/sync.py — manual "sync now" trigger and first-time LeetCode full
import.

/import_lc IS ONE SYNCHRONOUS USER ACTION:
  POST /import_lc
    -> runs bot_sheet_sync.py (unchanged import logic: CF+AtCoder+LeetCode
       fetch -> dedupe -> batch DB write -> batch Google Sheet write)
       as a blocking subprocess, in-request
    -> waits for it to actually finish
    -> parses its real stdout log lines (the [IMPORT]/[CODEFORCES]/
       [ATCODER]/[LEETCODE]/[DATABASE]/[GOOGLE SHEET] lines that script
       already prints) into real counts
    -> returns the REAL final result as one JSON response

There is no background thread, no worker handoff, no "started" status, and
no /import_lc/status polling endpoint. The request stays open for the full
duration of the import.

⚠️ Vercel note: this makes /import_lc a long-running synchronous request.
If the import takes longer than the deployed Vercel function's execution
limit, Vercel will return a 504 before this code gets a chance to respond,
regardless of what this file does. See vercel.json / Vercel project
settings for the actual configured limit.
"""

import os
import re
import subprocess
import sys

from flask import jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from services.sync_engine import sync_user_data
from config import Config


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

# Real log line bot_sheet_sync.py already prints once fetch+dedupe finish:
#   [IMPORT] COMPLETED total_elapsed=12.34s cf=5 lc=10 ac=3 unique_rows=18 db_new=8 sheet_rows=18
_COMPLETED_RE = re.compile(
    r"\[IMPORT\] COMPLETED total_elapsed=([\d.]+)s "
    r"cf=(\d+) lc=(\d+) ac=(\d+) unique_rows=(\d+) db_new=(\d+) sheet_rows=(\d+)"
)
_FAILED_STAGE_RE = re.compile(r"FAILED STAGE:\s*(\S+)")

# Hard ceiling so a broken cookie/hung request can't block the worker
# forever. Real value should match (or sit under) the deployed Vercel
# function's own execution limit — see the module docstring.
IMPORT_SUBPROCESS_TIMEOUT_SECONDS = int(os.environ.get("IMPORT_SUBPROCESS_TIMEOUT_SECONDS", "280"))


def _parse_import_output(output: str) -> dict:
    """Parses bot_sheet_sync.py's REAL stdout/stderr into a result dict.
    Never fabricates numbers — fields stay None if the expected log line
    isn't present (e.g. the process crashed before printing it)."""
    result = {
        "success": "STATUS: SUCCESS" in output,
        "cf_rows": None, "lc_rows": None, "ac_rows": None,
        "unique_rows": None, "db_new": None, "sheet_rows": None,
        "elapsed_seconds": None, "failed_stage": None,
    }

    m = _COMPLETED_RE.search(output)
    if m:
        result.update({
            "elapsed_seconds": float(m.group(1)),
            "cf_rows": int(m.group(2)),
            "lc_rows": int(m.group(3)),
            "ac_rows": int(m.group(4)),
            "unique_rows": int(m.group(5)),
            "db_new": int(m.group(6)),
            "sheet_rows": int(m.group(7)),
        })

    fm = _FAILED_STAGE_RE.search(output)
    if fm:
        result["failed_stage"] = fm.group(1)

    return result


@app.route("/import_lc", methods=["POST"])
@login_required
def import_lc():
    """
    ONE synchronous action: runs the real CF + AtCoder + LeetCode fetch ->
    dedupe -> batch DB write -> batch Google Sheet write (bot_sheet_sync.py,
    unchanged), waits for it to finish, and returns the real final result.
    No "started" response, no status polling.
    """
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=%s", (current_user.id,)
        ).fetchone()

    if not user or not user["lc_session_cookie"]:
        return jsonify({"success": False, "message": "Connect LeetCode first"})

    user_id = current_user.id

    # Atomically claim "running" so a double-click (or a retried request)
    # can't start a second concurrent import for the SAME user while one is
    # already in flight — this is now the only purpose of lc_import_status;
    # nothing polls it anymore.
    with get_db() as db:
        claimed = db.execute(
            """UPDATE users SET lc_import_status=%s
               WHERE id=%s AND (lc_import_status IS NULL OR lc_import_status NOT IN ('queued','running'))""",
            ("running", user_id)
        )
        row_claimed = claimed.rowcount > 0
        db.commit()

    if not row_claimed:
        return jsonify({
            "success": False,
            "message": "Import already in progress for this account. Please wait for it to finish."
        })

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"[import_lc] STARTED user_id={user_id} (synchronous)")

    try:
        proc = subprocess.run(
            [sys.executable, "bot_sheet_sync.py", str(user_id), "import"],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=IMPORT_SUBPROCESS_TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        output = (e.stdout or "") + "\n" + (e.stderr or "")
        with get_db() as db:
            db.execute("UPDATE users SET lc_import_status=%s WHERE id=%s", ("failed", user_id))
            db.commit()
        print(f"[import_lc] FAILED user_id={user_id} reason=timeout after {IMPORT_SUBPROCESS_TIMEOUT_SECONDS}s")
        return jsonify({
            "success": False,
            "message": f"Import timed out after {IMPORT_SUBPROCESS_TIMEOUT_SECONDS}s.",
            "failed_stage": "TIMEOUT",
        }), 504

    parsed = _parse_import_output(output)
    status = "completed" if parsed["success"] else "failed"

    with get_db() as db:
        db.execute("UPDATE users SET lc_import_status=%s WHERE id=%s", (status, user_id))
        db.commit()

    if parsed["success"]:
        print(f"[import_lc] COMPLETED user_id={user_id} "
              f"cf={parsed['cf_rows']} lc={parsed['lc_rows']} ac={parsed['ac_rows']} "
              f"unique={parsed['unique_rows']} db_new={parsed['db_new']} sheet_rows={parsed['sheet_rows']} "
              f"elapsed={parsed['elapsed_seconds']}s")
        message = (
            f"✅ Import complete — CF {parsed['cf_rows']}, LC {parsed['lc_rows']}, AC {parsed['ac_rows']} "
            f"→ {parsed['unique_rows']} unique problems, {parsed['db_new']} new rows saved, "
            f"{parsed['sheet_rows']} rows written to your sheet "
            f"({parsed['elapsed_seconds']:.1f}s)."
        )
        return jsonify({"success": True, "message": message, **parsed})
    else:
        stage = parsed["failed_stage"] or "UNKNOWN"
        print(f"[import_lc] FAILED user_id={user_id} stage={stage}")
        return jsonify({
            "success": False,
            "message": f"❌ Import failed at stage: {stage}.",
            **parsed,
        }), 500
