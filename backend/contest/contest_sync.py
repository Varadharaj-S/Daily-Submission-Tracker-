"""
contest/contest_sync.py — grades a Completed contest from stored
submissions (no live API calls) and writes results to the Google Sheet.

How grading works:
  The daily bot (normal_sync.py / bot_sheet_sync.py) pulls every accepted
  submission into the `submissions` table, which carries `submitted_at`
  (exact timestamp) alongside the older `solved_date` (text, day-only).
  submitted_at already existed in the live DB before this file did — it
  just wasn't in database/db.py's tracked schema (see migration 0005's
  comment) and wasn't reliably populated by every insert path. Rows with
  submitted_at = NULL (older rows, or ones that went through a path that
  didn't set it) still have solved_date, so we know *which day* they were
  solved on, just not the exact time.

  For a contest's problems (contest_problems), each student's solves on
  the contest's calendar date are split into two buckets:
    - in_window:    submitted_at falls inside [start_time, end_time], OR
                     submitted_at is NULL (time unknown — give it the
                     benefit of the doubt rather than losing the data)
    - after_window:  submitted_at is known and falls outside the window,
                     but still on the contest's date (solved late)

  The sheet shows this as "in_window(after_window)", e.g. "2(1)" — see
  contest_sheet.py. Rows without submitted_at can only ever produce
  in_window counts, never a bracket, since there's no time to compare
  against the window.

Entry points:
  run_due_contests()    — called by cron.py (Vercel cron) every 5 min
  sync_one_contest(c)   — called by admin 'Sync Now' button in routes/contest.py
"""

import traceback

from database.db import get_db
from contest import contest_service
from contest import contest_sheet
from contest.contest_utils import get_contest_window
from utils.helpers import _parse_any_date

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

def _solved_problem_details(db, user_id, platform, contest_date):
    """For each problem this student solved on this platform, on the
    contest's calendar date, returns {problem_id: submitted_at}.
    submitted_at is a datetime for rows where it was populated, or None
    for legacy rows that only ever recorded the date. Filtering by day is
    done in Python (via _parse_any_date) since solved_date's format isn't
    uniform across rows — a raw SQL date cast would blow up on the mix."""
    rows = db.execute("""
        SELECT DISTINCT problem_id, solved_date, submitted_at
        FROM submissions
        WHERE user_id = ?
          AND platform = ?
    """, (user_id, platform)).fetchall()

    details = {}
    for r in rows:
        dt = _parse_any_date(r["solved_date"])
        if dt and dt.date() == contest_date:
            submitted_at = r["submitted_at"]
            if submitted_at is not None and submitted_at.tzinfo is not None:
                # get_contest_window() returns naive datetimes — strip any
                # tzinfo here so the comparison in _split_in_after_window
                # can't blow up on "can't compare offset-naive and
                # offset-aware datetimes" regardless of the live column's
                # actual type (TIMESTAMP vs TIMESTAMPTZ).
                submitted_at = submitted_at.replace(tzinfo=None)
            details[r["problem_id"]] = submitted_at  # may be None
    return details


def _split_in_after_window(details, problem_codes, start_dt, end_dt):
    """details: {problem_id: submitted_at_or_None} for one student, already
    filtered to the contest's date. Returns (in_window_count, after_window_count)
    counting only problems that belong to this contest (problem_codes)."""
    in_window = 0
    after_window = 0
    for pid, submitted_at in details.items():
        if pid not in problem_codes:
            continue
        if submitted_at is None:
            # Legacy row — no exact time to compare, give benefit of the
            # doubt and count it as in-window rather than dropping it.
            in_window += 1
        elif start_dt <= submitted_at <= end_dt:
            in_window += 1
        else:
            after_window += 1
    return in_window, after_window


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
        contest_date = start_dt.date()
        participants = _get_participants(contest["platform"])

        results_by_user_id = {}
        with get_db() as db:
            solved_by_user = {}
            for p in participants:
                details = _solved_problem_details(
                    db, p["id"], contest["platform"], contest_date
                )
                in_window, after_window = _split_in_after_window(
                    details, problem_codes, start_dt, end_dt
                )
                solved_by_user[p["id"]] = (p["username"], in_window, after_window)

            # Local rank: most in-window solves first (after-window solves
            # don't count toward rank — they happened outside contest time),
            # ties broken alphabetically.
            ranked = sorted(
                [(uid, uname, iw) for uid, (uname, iw, aw) in solved_by_user.items() if iw > 0],
                key=lambda x: (-x[2], x[1])
            )
            rank_by_user = {uid: i + 1 for i, (uid, _, _) in enumerate(ranked)}

            for user_id, (username, in_window, after_window) in solved_by_user.items():
                participated  = (in_window + after_window) > 0
                rank          = rank_by_user.get(user_id)
                results_by_user_id[user_id] = {
                    "solved":        in_window,
                    "after_window":  after_window,
                    "participated":  participated,
                    "rank":          rank,
                    "score":         in_window,
                }

                db.execute("""
                    INSERT INTO contest_results
                        (contest_id, user_id, username, solved, after_window,
                         attendance, rank, score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())
                    ON CONFLICT (contest_id, user_id) DO UPDATE SET
                        solved=EXCLUDED.solved,
                        after_window=EXCLUDED.after_window,
                        attendance=EXCLUDED.attendance,
                        rank=EXCLUDED.rank,
                        score=EXCLUDED.score,
                        updated_at=NOW()
                """, (contest_id, user_id, username, in_window, after_window,
                      participated, rank, in_window))
            db.commit()

        # Write to Google Sheet
        sheet_ok, sheet_msg = contest_sheet.write_contest_results(
            contest["contest_code"], contest_date, results_by_user_id
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