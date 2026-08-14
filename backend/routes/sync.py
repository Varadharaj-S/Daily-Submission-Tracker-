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
import time
import traceback

from flask import jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from services.sync_engine import sync_user_data
from config import Config
from sync import chunked_import as ci


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


# ── /import_codeforces, /import_atcoder, /import_leetcode ───────────────────
#
# Three independent, single-request import actions. Each button click =
# exactly one HTTP request = the real final result in that one response.
# No /status endpoint, no polling, no background job. This is what fixes
# the 504: the old combined /import_lc ran CF+AtCoder+LeetCode in one
# request, and a first-time full LeetCode history fetch alone could run
# past the deployed Vercel function's execution limit. Splitting the
# platforms apart makes CF/AtCoder trivially fast single requests, and
# LeetCode below walks its history in bounded chunks — one manual button
# press per chunk — instead of trying to fetch everything in one shot.

def _run_single_shot_import(user, platform_key, fetch_fn, handle, missing_handle_message):
    """Shared body for /import_codeforces and /import_atcoder: one fetch,
    one dedupe, one DB batch write, one Sheet rebuild, one real result."""
    if not handle:
        return jsonify({"success": False, "message": missing_handle_message})

    t_start = time.time()
    platform_name = platform_key.upper()
    print("=" * 50)
    print(f"[{platform_name}] IMPORT STARTED")
    print(f"USER ID: {user['id']}")
    print(f"USERNAME: {user['username']}")
    print("=" * 50)

    rows, rows_fetched, fetch_error = fetch_fn(handle)
    if fetch_error and not rows:
        print(f"[{platform_name}] IMPORT FAILED error={fetch_error}")
        return jsonify({
            "success": False,
            "status": "failed",
            "platform": platform_key,
            "message": f"❌ {platform_name} fetch failed: {fetch_error}",
        }), 502

    df = ci.dedupe_platform_rows(rows)
    db_rows_written = ci.write_submissions_batch(user["id"], df)

    sheet_rows_written = 0
    sheet_error = None
    try:
        sheet_rows_written = ci.rebuild_user_sheet(
            user["id"], user["username"], user.get("email", ""), user.get("cohort_year")
        )
    except Exception as e:
        sheet_error = str(e)
        print(f"[{platform_name}] SHEET WRITE FAILED error={sheet_error}")
        print(traceback.format_exc().splitlines()[-1])

    elapsed = time.time() - t_start
    print(f"[{platform_name}] IMPORT COMPLETED")
    print("=" * 50)

    result = {
        "success": True,
        "status": "completed",
        "platform": platform_key,
        "rows_fetched": rows_fetched,
        "db_rows_written": db_rows_written,
        "sheet_rows_written": sheet_rows_written,
        "execution_time_seconds": round(elapsed, 2),
    }
    if sheet_error:
        result["sheet_warning"] = f"Data saved to database, but the Google Sheet write failed: {sheet_error}"
    result["message"] = (
        f"✅ {platform_name} import complete — {rows_fetched} fetched, "
        f"{db_rows_written} new rows saved" + (", sheet updated" if not sheet_error else ", ⚠️ sheet not updated")
        + f" ({elapsed:.1f}s)."
    )
    return jsonify(result)


def get_db_user(user_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
    return dict(row) if row else None


@app.route("/import_codeforces", methods=["POST"])
@login_required
def import_codeforces():
    user = get_db_user(current_user.id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    return _run_single_shot_import(
        user, "codeforces", ci.fetch_codeforces, user.get("cf_handle", ""),
        "No Codeforces handle configured. Add one in Settings first."
    )


@app.route("/import_atcoder", methods=["POST"])
@login_required
def import_atcoder():
    user = get_db_user(current_user.id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    return _run_single_shot_import(
        user, "atcoder", ci.fetch_atcoder, user.get("ac_handle", ""),
        "No AtCoder handle configured. Add one in Settings first."
    )


@app.route("/import_leetcode", methods=["POST"])
@login_required
def import_leetcode():
    """ONE chunk per request. Continues from the saved lc_import_offset —
    never restarts at 0 unless this is genuinely the first import for this
    user. The offset is only advanced AFTER this chunk's DB+Sheet writes
    succeed, so a failed request can be safely retried."""
    user = get_db_user(current_user.id)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    if not user.get("lc_session_cookie") or not user.get("lc_csrf_token"):
        return jsonify({"success": False, "message": "Connect LeetCode first"})

    user_id = user["id"]

    # Same "claim running" guard the old /import_lc used, so a double-click
    # can't advance the cursor twice from the same starting offset.
    with get_db() as db:
        claimed = db.execute(
            """UPDATE users SET lc_import_status=%s
               WHERE id=%s AND (lc_import_status IS NULL OR lc_import_status NOT IN ('queued','running'))""",
            ("running", user_id),
        )
        row_claimed = claimed.rowcount > 0
        db.commit()

    if not row_claimed:
        return jsonify({
            "success": False,
            "message": "A LeetCode import chunk is already in progress for this account. Please wait for it to finish."
        })

    t_start = time.time()
    start_offset = int(user.get("lc_import_offset") or 0)

    print("=" * 50)
    print("[LEETCODE] IMPORT STARTED")
    print(f"USER ID: {user_id}")
    print(f"USERNAME: {user['username']}")
    print("=" * 50)
    print("[LEETCODE] FETCH STARTED")

    try:
        session_cookie, csrf = ci.fetch_leetcode_cookie_from_db(user_id)
        lc_sess = ci.build_leetcode_session(session_cookie, csrf)
        logged_in, login_data = ci.verify_leetcode_login(lc_sess)
        if not logged_in:
            with get_db() as db:
                db.execute("UPDATE users SET lc_import_status=%s WHERE id=%s", ("failed", user_id))
                db.commit()
            print("[LEETCODE] IMPORT FAILED stage=LEETCODE_LOGIN")
            print("=" * 50)
            return jsonify({
                "success": False,
                "status": "failed",
                "platform": "leetcode",
                "message": "❌ LeetCode session could not be verified — your cookie may have expired. Reconnect in Settings.",
            }), 401

        rows, next_offset, has_more, fetch_error, cache_hits, cache_misses = ci.fetch_leetcode_chunk(
            lc_sess, start_offset
        )
    except Exception as e:
        with get_db() as db:
            db.execute("UPDATE users SET lc_import_status=%s WHERE id=%s", ("failed", user_id))
            db.commit()
        print(f"[LEETCODE] IMPORT FAILED error={e}")
        print(traceback.format_exc().splitlines()[-1])
        print("=" * 50)
        return jsonify({
            "success": False,
            "status": "failed",
            "platform": "leetcode",
            "message": f"❌ LeetCode fetch failed: {e}",
        }), 502

    if fetch_error and not rows and next_offset == start_offset:
        # Nothing usable happened this chunk — do NOT advance the cursor.
        with get_db() as db:
            db.execute("UPDATE users SET lc_import_status=%s WHERE id=%s", ("failed", user_id))
            db.commit()
        print(f"[LEETCODE] IMPORT FAILED error={fetch_error}")
        print("=" * 50)
        return jsonify({
            "success": False,
            "status": "failed",
            "platform": "leetcode",
            "message": f"❌ LeetCode fetch failed at offset {start_offset}: {fetch_error}",
        }), 502

    df = ci.dedupe_platform_rows(rows)
    db_rows_written = ci.write_submissions_batch(user_id, df)

    sheet_rows_written = 0
    sheet_error = None
    try:
        sheet_rows_written = ci.rebuild_user_sheet(
            user_id, user["username"], user.get("email", ""), user.get("cohort_year")
        )
    except Exception as e:
        sheet_error = str(e)
        print(f"[LEETCODE] SHEET WRITE FAILED error={sheet_error}")

    # Only NOW — after DB+Sheet writes for this chunk actually succeeded —
    # persist the new cursor position. A crash/timeout before this point
    # leaves lc_import_offset untouched, so the next press safely retries
    # the same page instead of skipping it.
    with get_db() as db:
        db.execute(
            """UPDATE users
               SET lc_import_offset=%s, lc_import_has_more=%s, lc_import_status=%s,
                   lc_imported = CASE WHEN %s THEN lc_imported ELSE 1 END
               WHERE id=%s""",
            (next_offset, 1 if has_more else 0, "completed" if not has_more else "partial",
             has_more, user_id),
        )
        db.commit()

    elapsed = time.time() - t_start
    print(f"[LEETCODE] IMPORT COMPLETED status={'partial' if has_more else 'completed'}")
    print("=" * 50)

    result = {
        "success": True,
        "status": "partial" if has_more else "completed",
        "platform": "leetcode",
        "rows_fetched": len(rows),
        "db_rows_written": db_rows_written,
        "sheet_rows_written": sheet_rows_written,
        "next_offset": next_offset,
        "has_more": has_more,
        "execution_time_seconds": round(elapsed, 2),
    }
    if fetch_error:
        result["fetch_warning"] = fetch_error
    if sheet_error:
        result["sheet_warning"] = f"Data saved to database, but the Google Sheet write failed: {sheet_error}"

    if has_more:
        result["message"] = (
            f"📥 LeetCode chunk done — {len(rows)} fetched, {db_rows_written} new rows saved "
            f"({elapsed:.1f}s). More history remains — press Import LeetCode again to continue."
        )
    else:
        result["message"] = (
            f"✅ LeetCode import complete — {len(rows)} fetched in this final chunk, "
            f"{db_rows_written} new rows saved ({elapsed:.1f}s)."
        )
    return jsonify(result)
