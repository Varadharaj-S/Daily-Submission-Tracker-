"""
contest/contest_service.py — Phase 1 scope: contest CRUD against
PostgreSQL only. No Google Sheets (contest_sheet.py, Phase 2), no
platform API calls (contest_api.py / contest_sync.py, Phase 3).

Every function takes plain values in / returns plain dict-like DB rows
out — routes/contest.py is the only caller and does its own sanitize()
on form input before it reaches here, matching the pattern the rest of
the app already uses (routes sanitize, services trust their callers).
"""

from datetime import date, time, datetime

from database.db import get_db
from contest.contest_utils import compute_status, normalize_contest_code


def _serialize_row(d):
    """Convert DATE/TIME/TIMESTAMPTZ columns (psycopg2 hands these back as
    real date/time/datetime objects) into plain strings.

    ROOT CAUSE of "TypeError: Object of type time is not JSON serializable":
    contest_events.contest_date is a DATE column and start_time/end_time are
    TIME columns (see migrations/0002_contest_tracker.sql) — Postgres/psycopg2
    return those as datetime.date / datetime.time objects, not strings. Flask's
    jsonify() calls Python's stdlib json.dumps(), which has no idea how to
    serialize those types and throws. Every dict this module hands back to a
    route that calls jsonify(...) needs to go through this first.
    """
    out = dict(d)
    for k, v in out.items():
        if isinstance(v, time):
            out[k] = v.strftime("%H:%M")   # "14:30" instead of "14:30:00"
        elif isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
    return out


def ensure_contest_schema():
    """Create the contest tables/columns if they don't exist yet.

    ROOT CAUSE of the 500 on /student_contest: database/migrate.py (which
    creates contest_events, contest_results, contest_problems — see
    migrations/0002_contest_tracker.sql and 0006_contest_phase3_schema.sql)
    is a standalone script. Nobody runs it against the Vercel production
    DATABASE_URL automatically — app.py's init_db() (which DOES run on
    every cold start, including under wsgi.py/gunicorn on Vercel) never
    created these tables, since they were added later as a separate
    migration. So every request to /student_contest ran
    "SELECT * FROM contest_events" against a table that simply doesn't
    exist in production yet, which Postgres rejects and Flask turns into
    an uncaught-exception 500.

    This mirrors init_db()'s own CREATE TABLE IF NOT EXISTS pattern, so it's
    idempotent and safe to call unconditionally on every startup — see
    app.py, where it's now called right after init_db(), instead of only
    being reachable by manually running database/migrate.py locally.
    """
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS contest_events (
            id           SERIAL PRIMARY KEY,
            contest_name TEXT        NOT NULL,
            contest_code TEXT        NOT NULL,
            platform     TEXT        NOT NULL,
            contest_date DATE        NOT NULL,
            start_time   TIME        NOT NULL,
            end_time     TIME        NOT NULL,
            status       TEXT        NOT NULL DEFAULT 'Upcoming',
            sheet_name   TEXT        DEFAULT 'Student_Contest',
            created_by   INTEGER     REFERENCES users(id) ON DELETE SET NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (contest_code)
        );
        CREATE INDEX IF NOT EXISTS idx_contest_events_date   ON contest_events(contest_date DESC);
        CREATE INDEX IF NOT EXISTS idx_contest_events_status ON contest_events(status);

        CREATE TABLE IF NOT EXISTS contest_results (
            id           SERIAL PRIMARY KEY,
            contest_id   INTEGER     NOT NULL REFERENCES contest_events(id) ON DELETE CASCADE,
            user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            username     TEXT        NOT NULL,
            solved       INTEGER     DEFAULT 0,
            attendance   BOOLEAN     DEFAULT false,
            rank         INTEGER,
            score        NUMERIC,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (contest_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_contest_results_contest ON contest_results(contest_id);
        CREATE INDEX IF NOT EXISTS idx_contest_results_user    ON contest_results(user_id);

        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS synced          BOOLEAN     NOT NULL DEFAULT FALSE;
        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS sync_attempts   INTEGER     NOT NULL DEFAULT 0;
        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS last_sync_error TEXT;
        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS sync_claimed_at TIMESTAMPTZ;
        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS last_synced_at  TIMESTAMPTZ;
        ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS idx_contest_events_synced ON contest_events(synced, sync_attempts);

        CREATE TABLE IF NOT EXISTS contest_sync_log (
            id          SERIAL  PRIMARY KEY,
            contest_id  INTEGER NOT NULL REFERENCES contest_events(id) ON DELETE CASCADE,
            status      TEXT    NOT NULL,
            retry_count INTEGER DEFAULT 0,
            last_error  TEXT,
            message     TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_contest_sync_log_contest ON contest_sync_log(contest_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS contest_problems (
            id          SERIAL  PRIMARY KEY,
            contest_id  INTEGER NOT NULL REFERENCES contest_events(id) ON DELETE CASCADE,
            problem_id  TEXT    NOT NULL,
            platform    TEXT    NOT NULL,
            added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contest_problems_unique
            ON contest_problems(contest_id, problem_id);
        CREATE INDEX IF NOT EXISTS idx_contest_problems_contest
            ON contest_problems(contest_id);
        """)
        db.commit()


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


def list_contests(status=None):
    """Returns all contests, freshest first, with status recomputed live
    (not trusted from the stored column — see contest_utils.compute_status).
    Every dict is passed through _serialize_row() so DATE/TIME columns are
    plain strings by the time a route hands this to jsonify()."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM contest_events ORDER BY contest_date DESC, start_time DESC"
        ).fetchall()

    contests = []
    for r in rows:
        d = dict(r)
        d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
        contests.append(_serialize_row(d))

    if status:
        contests = [c for c in contests if c["status"] == status]
    return contests


def get_contest(contest_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM contest_events WHERE id=?", (contest_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
    return _serialize_row(d)


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


# ── Phase 3 grading support ───────────────────────────────────────────────
# contest_sync.py has always called these five functions, and the
# 'Sync Now' flow always needed a way to add a contest's problem list —
# but none of it was ever actually written, which is the real reason
# results never made it into the sheet (see routes/contest.py's sync_now
# route and contest_sync.py's own module docstring for the full pipeline).

def get_due_contests():
    """Completed-by-computed-status contests that haven't synced yet,
    oldest first. Deliberately returns raw (non-serialized) rows — this
    is only ever consumed internally by contest_sync.py, which needs real
    date/time objects, not the string form _serialize_row() produces for
    JSON routes."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM contest_events WHERE synced = FALSE ORDER BY contest_date ASC, start_time ASC"
        ).fetchall()
    due = []
    for r in rows:
        d = dict(r)
        d["status"] = compute_status(d["contest_date"], d["start_time"], d["end_time"])
        if d["status"] == "Completed":
            due.append(d)
    return due


def get_contest_problems(contest_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM contest_problems WHERE contest_id=? ORDER BY added_at ASC",
            (contest_id,)
        ).fetchall()
    return [_serialize_row(dict(r)) for r in rows]


def add_problem_to_contest(contest_id, problem_id, platform):
    problem_id = (problem_id or "").strip()
    if not problem_id:
        return False, "Problem ID/code is required."
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM contest_problems WHERE contest_id=? AND problem_id=?",
            (contest_id, problem_id)
        ).fetchone()
        if existing:
            return False, f"'{problem_id}' is already on this contest's problem list."
        db.execute(
            "INSERT INTO contest_problems (contest_id, problem_id, platform) VALUES (?,?,?)",
            (contest_id, problem_id, platform)
        )
        db.commit()
    return True, f"'{problem_id}' added. Click 'Sync Now' to (re)grade against it."


def remove_problem_from_contest(contest_id, problem_id):
    with get_db() as db:
        db.execute(
            "DELETE FROM contest_problems WHERE contest_id=? AND problem_id=?",
            (contest_id, (problem_id or "").strip())
        )
        db.commit()


def force_resync(contest_id):
    """Resets a contest's sync state so 'Sync Now' (or the next scheduler
    tick) grades it fresh — clears a stale claim, a 'gave up after max
    retries' state, or just re-grades after the problem list changed."""
    with get_db() as db:
        db.execute("""
            UPDATE contest_events
            SET synced=FALSE, sync_attempts=0, sync_claimed_at=NULL, last_sync_error=NULL
            WHERE id=?
        """, (contest_id,))
        db.commit()
