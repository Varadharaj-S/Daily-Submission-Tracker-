"""
contest/contest_service.py — Phase 1 scope: contest CRUD against
PostgreSQL only. No Google Sheets (contest_sheet.py, Phase 2), no
platform API calls (contest_api.py / contest_sync.py, Phase 3).

Every function takes plain values in / returns plain dict-like DB rows
out — routes/contest.py is the only caller and does its own sanitize()
on form input before it reaches here, matching the pattern the rest of
the app already uses (routes sanitize, services trust their callers).
"""

from database.db import get_db
from contest.contest_utils import compute_status, normalize_contest_code


def create_contest(contest_name, contest_code, platform, contest_date,
                    start_time, end_time, created_by):
    contest_code = normalize_contest_code(contest_code)
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM contest_events WHERE contest_code=?", (contest_code,)
        ).fetchone()
        if existing:
            return None, f"Contest code '{contest_code}' already exists."

        db.execute("""
            INSERT INTO contest_events
            (contest_name, contest_code, platform, contest_date, start_time, end_time,
             status, sheet_name, created_by)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (contest_name, contest_code, platform, contest_date, start_time, end_time,
              compute_status(contest_date, start_time, end_time),
              "Student_Contest", created_by))
        db.commit()

        row = db.execute(
            "SELECT * FROM contest_events WHERE contest_code=?", (contest_code,)
        ).fetchone()
    return row, None


def _serialize_contest(d):
    """Convert date/time objects → strings so jsonify() works."""
    import datetime
    for k in ("contest_date", "start_time", "end_time",
              "created_at", "updated_at", "last_attempt_at", "sync_claimed_at"):
        v = d.get(k)
        if isinstance(v, datetime.datetime):
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, datetime.date):
            d[k] = v.strftime("%Y-%m-%d")
        elif isinstance(v, datetime.time):
            d[k] = v.strftime("%H:%M")
    return d


def list_contests(status=None):
    """Returns all contests, freshest first, with status recomputed live
    (not trusted from the stored column — see contest_utils.compute_status)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM contest_events ORDER BY contest_date DESC, start_time DESC"
        ).fetchall()

    contests = []
    for r in rows:
        d = _serialize_contest(dict(r))
        d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
        contests.append(d)

    if status:
        contests = [c for c in contests if c["status"] == status]
    return contests


def get_contest(contest_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM contest_events WHERE id=?", (contest_id,)).fetchone()
    if not row:
        return None
    d = _serialize_contest(dict(row))
    d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
    return d


def update_contest(contest_id, contest_name=None, platform=None,
                    contest_date=None, start_time=None, end_time=None):
    fields, params = [], []
    for col, val in [("contest_name", contest_name), ("platform", platform),
                      ("contest_date", contest_date), ("start_time", start_time),
                      ("end_time", end_time)]:
        if val is not None:
            fields.append(f"{col}=?")
            params.append(val)
    if not fields:
        return False
    params.append(contest_id)
    with get_db() as db:
        db.execute(f"UPDATE contest_events SET {', '.join(fields)} WHERE id=?", params)
        db.commit()
    return True


def delete_contest(contest_id):
    with get_db() as db:
        db.execute("DELETE FROM contest_events WHERE id=?", (contest_id,))
        db.commit()


def sync_status_column(contest_id):
    """Persist the freshly-computed status onto the stored column, so
    other tools reading the table directly (e.g. a future Sheet formula
    or a report query) see an up-to-date value too. Routes call this
    after create/edit; Phase 3's scheduler calls it as contests transition
    into 'Running'/'Completed'."""
    c = get_contest(contest_id)
    if not c:
        return
    with get_db() as db:
        db.execute("UPDATE contest_events SET status=? WHERE id=?", (c["status"], contest_id))
        db.commit()


# ── Phase 3 additions: sync support ──────────────────────────────────────────

def get_due_contests():
    """Returns all Completed-but-unsynced contests (synced=FALSE) that
    haven't exceeded MAX_SYNC_ATTEMPTS. Called by contest_sync.run_due_contests()."""
    with get_db() as db:
        rows = db.execute("""
            SELECT * FROM contest_events
            WHERE synced = FALSE
              AND (sync_attempts IS NULL OR sync_attempts < 5)
            ORDER BY contest_date ASC, start_time ASC
        """).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
        result.append(d)
    # Only return contests that are actually Completed
    return [c for c in result if c["status"] == "Completed"]


def get_contest_problems(contest_id):
    """Returns list of {problem_id, platform} for a contest.
    problem_id = the platform's own identifier (e.g. CF problem code,
    LC title slug, AC problem id) — used by contest_sync to intersect
    with the submissions table."""
    with get_db() as db:
        rows = db.execute("""
            SELECT problem_id, platform
            FROM contest_problems
            WHERE contest_id = ?
        """, (contest_id,)).fetchall()
    return [dict(r) for r in rows]


def force_resync(contest_id):
    """Resets synced=FALSE and sync_attempts=0 so the next scheduler tick
    (or an admin's 'Sync Now') retries the contest from scratch."""
    with get_db() as db:
        db.execute("""
            UPDATE contest_events
            SET synced=FALSE, sync_attempts=0, last_sync_error=NULL,
                sync_claimed_at=NULL
            WHERE id=?
        """, (contest_id,))
        db.commit()


def add_problem_to_contest(contest_id, problem_id, platform):
    """Adds a problem to a contest's problem list (contest_problems table).
    Idempotent — silently ignores duplicates."""
    with get_db() as db:
        db.execute("""
            INSERT INTO contest_problems (contest_id, problem_id, platform)
            VALUES (?, ?, ?)
            ON CONFLICT (contest_id, problem_id) DO NOTHING
        """, (contest_id, problem_id, platform))
        db.commit()


def remove_problem_from_contest(contest_id, problem_id):
    with get_db() as db:
        db.execute("""
            DELETE FROM contest_problems WHERE contest_id=? AND problem_id=?
        """, (contest_id, problem_id))
        db.commit()