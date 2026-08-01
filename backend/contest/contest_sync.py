"""
contest/contest_sync.py — grades a Completed contest from stored
submissions (no live API calls) and writes results to the Google Sheet.

How grading works:
  The daily bot (normal_sync.py) already pulls every accepted submission
  into the `submissions` table with submitted_at timestamps. Grading a
  contest = one SQL query: which of this contest's problems (contest_problems)
  does each student have a submission row for, with submitted_at inside
  [contest_start, contest_end]?

Entry points:
  run_due_contests()    — called by cron.py (Vercel cron) every 5 min
  sync_one_contest(c)   — called by admin 'Sync Now' button in routes/contest.py
"""

import traceback

from database.db import get_db
from contest import contest_service
from contest import contest_sheet
from contest.contest_utils import get_contest_window

MAX_SYNC_ATTEMPTS = 5

PLATFORM_HANDLE_COLUMN = {
    "Codeforces": "cf_handle",
    "LeetCode":   "lc_handle",
    "AtCoder":    "ac_handle",
}


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(contest_id, status, retry_count, last_error="", message=""):
    try:
        with get_db() as db:
            db.execute("""
                INSERT INTO contest_sync_log
                    (contest_id, status, retry_count, last_error, message, created_at)
                VALUES (?, ?, ?, ?, ?, NOW())
            """, (contest_id, status, retry_count, last_error, message))
            db.commit()
    except Exception as e:
        print(f"[ContestSync] log failed: {e}")


# ── Atomic claim (race-safe) ──────────────────────────────────────────────────

def _claim_contest(contest_id):
    """Atomically marks a contest as 'being synced'.
    Returns True only if this call won the claim.
    Claims older than 10 min are treated as abandoned and can be reclaimed."""
    with get_db() as db:
        row = db.execute("""
            UPDATE contest_events
            SET sync_claimed_at = NOW(), last_attempt_at = NOW()
            WHERE id = ?
              AND synced = FALSE
              AND (sync_claimed_at IS NULL
                   OR sync_claimed_at < NOW() - INTERVAL '10 minutes')
            RETURNING id
        """, (contest_id,)).fetchone()
        db.commit()
    return bool(row)


# ── Participants ──────────────────────────────────────────────────────────────

def _get_participants(platform):
    """Active non-admin students with a handle set for this platform."""
    handle_col = PLATFORM_HANDLE_COLUMN.get(platform)
    if not handle_col:
        return []
    with get_db() as db:
        rows = db.execute(f"""
            SELECT id, username, {handle_col} AS handle
            FROM users
            WHERE status='active' AND is_admin=FALSE
              AND {handle_col} IS NOT NULL AND {handle_col} != ''
        """).fetchall()
    return [dict(r) for r in rows]


# ── Submission lookup ─────────────────────────────────────────────────────────

def _solved_problem_codes(db, user_id, platform, start_dt, end_dt):
    """Problem IDs this student solved on this platform within the window."""
    rows = db.execute("""
        SELECT DISTINCT problem_id
        FROM submissions
        WHERE user_id = ?
          AND platform = ?
          AND submitted_at IS NOT NULL
          AND submitted_at BETWEEN ? AND ?
    """, (user_id, platform, start_dt, end_dt)).fetchall()
    return {r["problem_id"] for r in rows}


# ── Core sync ─────────────────────────────────────────────────────────────────

def sync_one_contest(contest):
    """
    Grades one contest and writes results to the sheet.
    contest: dict from contest_service.get_contest() / get_due_contests().
    Returns (ok: bool, message: str). Never raises.
    """
    contest_id = contest["id"]
    attempts   = (contest.get("sync_attempts") or 0) + 1

    if not _claim_contest(contest_id):
        return False, "Could not claim contest (already syncing or already synced)."

    _log(contest_id, "running", attempts, message="Sync started")

    try:
        problems = contest_service.get_contest_problems(contest_id)
        problem_codes = {p["problem_id"] for p in problems}

        if not problem_codes:
            msg = (
                "No problems configured for this contest — add them on the "
                "contest page (Problem List), then Sync Now."
            )
            with get_db() as db:
                db.execute("""
                    UPDATE contest_events
                    SET sync_attempts=?, last_sync_error=?, sync_claimed_at=NULL
                    WHERE id=?
                """, (attempts, msg, contest_id))
                db.commit()
            _log(contest_id, "failed", attempts, msg, msg)
            return False, msg

        start_dt, end_dt = get_contest_window(contest)
        participants = _get_participants(contest["platform"])

        results_by_user_id = {}
        with get_db() as db:
            solved_by_user = {}
            for p in participants:
                solved = _solved_problem_codes(
                    db, p["id"], contest["platform"], start_dt, end_dt
                )
                solved_by_user[p["id"]] = (p["username"], solved & problem_codes)

            # Local rank: most solved first, ties broken alphabetically
            ranked = sorted(
                [(uid, uname, s) for uid, (uname, s) in solved_by_user.items() if s],
                key=lambda x: (-len(x[2]), x[1])
            )
            rank_by_user = {uid: i + 1 for i, (uid, _, _) in enumerate(ranked)}

            for user_id, (username, solved_set) in solved_by_user.items():
                solved_count  = len(solved_set)
                participated  = solved_count > 0
                rank          = rank_by_user.get(user_id)
                results_by_user_id[user_id] = {
                    "solved":       solved_count,
                    "participated": participated,
                    "rank":         rank,
                    "score":        solved_count,
                }

                db.execute("""
                    INSERT INTO contest_results
                        (contest_id, user_id, username, solved, attendance, rank, score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NOW())
                    ON CONFLICT (contest_id, user_id) DO UPDATE SET
                        solved=EXCLUDED.solved,
                        attendance=EXCLUDED.attendance,
                        rank=EXCLUDED.rank,
                        score=EXCLUDED.score,
                        updated_at=NOW()
                """, (contest_id, user_id, username, solved_count, participated, rank, solved_count))
            db.commit()

        # Write to Google Sheet
        sheet_ok, sheet_msg = contest_sheet.write_contest_results(
            contest["contest_code"], results_by_user_id
        )

        with get_db() as db:
            db.execute("""
                UPDATE contest_events
                SET synced=TRUE, sync_attempts=?, last_sync_error=?, sync_claimed_at=NULL
                WHERE id=?
            """, (attempts, "" if sheet_ok else sheet_msg, contest_id))
            db.commit()

        msg = (
            f"{len(results_by_user_id)} participant(s) graded. {sheet_msg}"
        )
        _log(
            contest_id,
            "success" if sheet_ok else "failed",
            attempts,
            "" if sheet_ok else sheet_msg,
            msg
        )
        return sheet_ok, msg

    except Exception as e:
        err      = str(e)
        give_up  = attempts >= MAX_SYNC_ATTEMPTS
        with get_db() as db:
            db.execute("""
                UPDATE contest_events
                SET synced=?, sync_attempts=?, last_sync_error=?, sync_claimed_at=NULL
                WHERE id=?
            """, (give_up, attempts, err, contest_id))
            db.commit()
        _log(contest_id, "failed", attempts, err, traceback.format_exc()[-2000:])
        note = (
            " (gave up — use 'Sync Now' to retry manually)"
            if give_up else
            " (will retry on next cron tick)"
        )
        return False, f"Sync failed: {err}{note}"


# ── Scheduler entry point ─────────────────────────────────────────────────────

def run_due_contests():
    """
    Called every 5 min by routes/cron.py (Vercel cron) or contest_scheduler.py
    (Render cron). Finds every Completed+unsynced contest and syncs each one.
    """
    contests = contest_service.get_due_contests()
    results  = []
    for c in contests:
        ok, msg = sync_one_contest(c)
        results.append((c["contest_code"], ok, msg))
        print(f"[ContestSync] {c['contest_code']}: {'OK' if ok else 'FAIL'} — {msg}")
    return results
