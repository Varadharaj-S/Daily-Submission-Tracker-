"""
contest/contest_sync.py — Phase 3/6: turns a Completed-but-unsynced contest
into filled contest_results rows, an updated Student_Contest sheet column,
and recalculated attendance — automatically, on a timer, with retries.

DESIGN — no platform API calls, ever (Phase 6 redesign):

The old version of this file called each judge's contest-standings API
after the contest ended (contest_api.py). That's gone. The DSA bot
(normal_sync.py) already pulls every accepted submission daily into the
`submissions` table, timestamped to the second (submitted_at — see
migration 0007). So grading a contest is just:

    for each participant:
        which of THIS contest's problems (contest_problems) does this
        student have a submissions row for, with submitted_at falling
        inside [contest_start, contest_end]?
        -> that count is their solved count for the contest.

No login, no cookies, no Cloudflare, no rate limits, no "the judge changed
their JSON shape" breakage, and it works even if the app has zero network
access at sync time — it's one SQL query per participant against data
that's already sitting in Postgres.

Entry points:
  run_due_contests() — the scheduler tick (contest_scheduler.py, every
    5 minutes via Render Cron; see render.yaml). Finds every due contest
    and syncs each one.
  sync_one_contest(contest) — syncs a single contest. Also what the admin
    "Sync Now" button (routes/contest.py) calls directly, after
    contest_service.force_resync(), for an immediate manual retry instead
    of waiting for the next tick.

Pipeline per contest:

    claim (atomic, race-safe)
        -> load this contest's problem list (contest_problems) — if empty,
           there's nothing to grade against, so this fails loudly instead
           of silently reporting everyone as 0-solved
        -> compute the contest's [start, end] window (contest_utils.get_contest_window)
        -> for each participant, one SQL query: submissions in that window,
           on that platform, intersected with this contest's problem codes
        -> upsert contest_results in PostgreSQL (one transaction)
        -> write the results column + recalc attendance/summary on the
           Google Sheet (contest_sheet.write_contest_results — one batch
           call)
        -> mark contest_events.synced = TRUE

A student counts as having "attended" if they solved at least one of the
contest's problems inside the window. There's no separate "attempted but
didn't solve" signal available — `submissions` only ever holds accepted
solves (normal_sync.py's fetchers skip non-OK verdicts) — so solved>0 is
the only participation signal there is, same as the design this replaces.

Never raises out of sync_one_contest — every failure is caught, logged to
contest_sync_log (Issue 7), and left for the next tick to retry, up to
MAX_SYNC_ATTEMPTS.
"""

import traceback

from database.db import get_db
from contest import contest_service
from contest import contest_sheet
from contest.contest_utils import get_contest_window

MAX_SYNC_ATTEMPTS = 5

PLATFORM_HANDLE_COLUMN = {
    "Codeforces": "cf_handle",
    "LeetCode": "lc_handle",
    "AtCoder": "ac_handle",
}


def _log(contest_id, status, retry_count, last_error="", message=""):
    with get_db() as db:
        db.execute("""
            INSERT INTO contest_sync_log (contest_id, status, retry_count, last_error, message)
            VALUES (?,?,?,?,?)
        """, (contest_id, status, retry_count, last_error, message))
        db.commit()


def _claim_contest(contest_id):
    """Atomically claim a contest for syncing so two overlapping calls
    (a scheduler tick racing an admin's manual 'Sync Now', or two gunicorn
    workers both ticking) never process the same contest twice. A claim
    older than 10 minutes is treated as abandoned (a crashed/timed-out
    worker mid-sync shouldn't permanently block retries) and can be
    reclaimed."""
    with get_db() as db:
        row = db.execute("""
            UPDATE contest_events
            SET sync_claimed_at = now(), last_attempt_at = now()
            WHERE id = ?
              AND synced = FALSE
              AND (sync_claimed_at IS NULL OR sync_claimed_at < now() - INTERVAL '10 minutes')
            RETURNING id
        """, (contest_id,)).fetchone()
        db.commit()
    return bool(row)


def _get_participants(platform):
    """Active, non-admin users with a handle set for this platform —
    exactly who Issue 2's users.<platform>_handle columns exist for."""
    handle_col = PLATFORM_HANDLE_COLUMN.get(platform)
    if not handle_col:
        return []
    with get_db() as db:
        rows = db.execute(f"""
            SELECT id, username, {handle_col} AS handle
            FROM users
            WHERE status='active' AND is_admin=0
              AND {handle_col} IS NOT NULL AND {handle_col} != ''
        """).fetchall()
    return [dict(r) for r in rows]


def _solved_problem_codes(db, user_id, platform, start_dt, end_dt):
    """Every distinct problem_id this student has an accepted-submission row
    for on this platform, with submitted_at inside the contest window.
    Pure SQL against already-collected data — no network call."""
    rows = db.execute("""
        SELECT DISTINCT problem_id
        FROM submissions
        WHERE user_id = ?
          AND platform = ?
          AND submitted_at IS NOT NULL
          AND submitted_at BETWEEN ? AND ?
    """, (user_id, platform, start_dt, end_dt)).fetchall()
    return {r["problem_id"] for r in rows}


def sync_one_contest(contest):
    """Runs the full pipeline for one contest dict (as returned by
    contest_service.get_due_contests()/get_contest()). Returns
    (ok: bool, message: str)."""
    contest_id = contest["id"]
    attempts = (contest.get("sync_attempts") or 0) + 1

    if not _claim_contest(contest_id):
        return False, "Could not claim contest (already syncing, or already synced)."

    _log(contest_id, "running", attempts, message="Sync started")

    try:
        problems = contest_service.get_contest_problems(contest_id)
        problem_codes = {p["problem_id"] for p in problems}
        if not problem_codes:
            msg = ("No problems configured for this contest — add them on the contest "
                   "page (Problem List), then Sync Now. Nothing to grade against yet.")
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

        # ── PostgreSQL: grade from stored submissions, upsert contest_results ──
        results_by_user_id = {}
        with get_db() as db:
            solved_by_user = {}
            for p in participants:
                solved = _solved_problem_codes(db, p["id"], contest["platform"], start_dt, end_dt)
                solved_by_user[p["id"]] = (p["username"], solved & problem_codes)

            # Local leaderboard rank among participants who solved at least
            # one problem — there's no judge "official" rank available
            # offline. Highest solved count first; ties broken by username
            # for a stable order.
            ranked = sorted(
                [(uid, uname, s) for uid, (uname, s) in solved_by_user.items() if s],
                key=lambda x: (-len(x[2]), x[1])
            )
            rank_by_user = {uid: i + 1 for i, (uid, _, _) in enumerate(ranked)}

            for user_id, (username, solved_set) in solved_by_user.items():
                solved_count = len(solved_set)
                participated = solved_count > 0
                rank = rank_by_user.get(user_id)
                results_by_user_id[user_id] = {
                    "solved": solved_count,
                    "participated": participated,
                    "rank": rank,
                    "score": solved_count,
                }

                db.execute("""
                    INSERT INTO contest_results
                    (contest_id, user_id, username, solved, attendance, rank, score, updated_at)
                    VALUES (?,?,?,?,?,?,?, now())
                    ON CONFLICT (contest_id, user_id) DO UPDATE SET
                        solved=EXCLUDED.solved, attendance=EXCLUDED.attendance,
                        rank=EXCLUDED.rank, score=EXCLUDED.score, updated_at=now()
                """, (contest_id, user_id, username, solved_count, participated, rank, solved_count))
            db.commit()

        # ── Google Sheet: batch column write + attendance/summary recalc ──
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

        msg = f"{len(results_by_user_id)} participant(s) graded from stored submissions. {sheet_msg}"
        # A Sheets failure still counts as a completed sync attempt (the DB
        # side succeeded) — logged as 'failed' so it's visible, but not
        # left un-synced forever just because Sheets hiccuped; use
        # 'Sync Now' or 'Refresh Sheet' to retry the Sheets side alone.
        _log(contest_id, "success" if sheet_ok else "failed", attempts,
             "" if sheet_ok else sheet_msg, msg)
        return sheet_ok, msg

    except Exception as e:
        err = str(e)
        give_up = attempts >= MAX_SYNC_ATTEMPTS
        with get_db() as db:
            db.execute("""
                UPDATE contest_events
                SET synced=?, sync_attempts=?, last_sync_error=?, sync_claimed_at=NULL
                WHERE id=?
            """, (give_up, attempts, err, contest_id))
            db.commit()
        _log(contest_id, "failed", attempts, err, traceback.format_exc()[-2000:])
        note = " (gave up after max retries — use 'Sync Now' to retry manually)" if give_up else \
               " (will retry on the next scheduler tick)"
        return False, f"Sync failed: {err}{note}"


def run_due_contests():
    """Scheduler entry point (contest_scheduler.py). Finds every
    Completed-but-unsynced contest and syncs it. Safe to call every 5
    minutes — contests that are already synced are skipped by the
    get_due_contests() filter itself, so most ticks do nothing."""
    contests = contest_service.get_due_contests()
    results = []
    for c in contests:
        ok, msg = sync_one_contest(c)
        results.append((c["contest_code"], ok, msg))
        print(f"[ContestSync] {c['contest_code']}: {'OK' if ok else 'FAIL'} — {msg}")
    return results
