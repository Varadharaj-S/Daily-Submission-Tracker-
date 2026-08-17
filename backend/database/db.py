"""
db.py — Centralized PostgreSQL database access for DSA Tracker.

This project has been fully migrated off SQLite. Every part of the app
(app.py, bot.py, scheduler.py, normal_sync.py, bot_sheet_sync.py,
sync/sync_service.py, and the one-off maintenance scripts) now goes
through get_db() in this file, which always returns a PostgreSQL
connection.

Set the DATABASE_URL environment variable to a PostgreSQL connection
string, e.g.:

    postgresql://user:password@host:5432/dsa_tracker

On Render, DATABASE_URL is injected automatically (see render.yaml).
For local development, install PostgreSQL and export DATABASE_URL
yourself, e.g.:

    export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/dsa_tracker"
"""

import os
import psycopg2
import psycopg2.extras
import psycopg2.errors

IntegrityError = psycopg2.IntegrityError  # re-exported for callers that used to catch sqlite3.IntegrityError

_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _normalize_url(url: str) -> str:
    # Some providers (e.g. Render/Heroku-style) hand out "postgres://" but
    # psycopg2 wants "postgresql://".
    return url.replace("postgres://", "postgresql://", 1)


def _require_url() -> str:
    if not _DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. This project requires PostgreSQL — "
            "set the DATABASE_URL environment variable to your Postgres "
            "connection string (see db.py for an example)."
        )
    return _normalize_url(_DATABASE_URL)


class Cursor:
    """Wraps a psycopg2 RealDictCursor so old sqlite-style call sites
    (using '?' placeholders and dict-like row access) keep working."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        pg_sql = sql.replace("?", "%s")
        self._cur.execute(pg_sql, params)
        return self

    def executemany(self, sql, seq_of_params):
        pg_sql = sql.replace("?", "%s")
        self._cur.executemany(pg_sql, seq_of_params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class Connection:
    """Wraps a psycopg2 connection so old sqlite3.Connection call sites
    (execute directly on the connection, executescript, context-manager
    commit/rollback-on-exit) keep working unchanged."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return Cursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executescript(self, script):
        """Run a multi-statement SQL script (schema creation, migrations)."""
        cur = self._conn.cursor()
        cur.execute(script)
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            try:
                self._conn.commit()
            except Exception:
                pass
        self._conn.close()
        return False


def get_db() -> Connection:
    """Returns a PostgreSQL connection, wrapped to behave like the old
    sqlite3 connections this project used to use."""
    conn = psycopg2.connect(_require_url())
    conn.autocommit = False
    return Connection(conn)


def init_db():
    """Create all tables if they don't already exist (PostgreSQL syntax)."""
    from werkzeug.security import generate_password_hash
    from datetime import datetime

    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                SERIAL PRIMARY KEY,
            username          TEXT UNIQUE NOT NULL,
            email             TEXT DEFAULT '',
            password          TEXT NOT NULL,
            is_admin          INTEGER DEFAULT 0,
            is_verified       INTEGER DEFAULT 0,
            status            TEXT DEFAULT 'pending',
            sheet_id          TEXT DEFAULT '',
            cf_handle         TEXT DEFAULT '',
            lc_handle         TEXT DEFAULT '',
            lc_password       TEXT DEFAULT '',
            lc_session_cookie TEXT DEFAULT '',
            lc_csrf_token     TEXT DEFAULT '',
            cookie_expiry     INTEGER DEFAULT 0,
            ac_handle         TEXT DEFAULT '',
            enabled_platforms TEXT DEFAULT '["Codeforces","LeetCode","AtCoder"]',
            bio               TEXT DEFAULT '',
            is_public         INTEGER DEFAULT 1,
            last_login        TEXT DEFAULT '',
            last_sync         TEXT DEFAULT '',
            created_at        TEXT DEFAULT '',
            lc_imported       INTEGER DEFAULT 0,
            auto_sync_enabled INTEGER DEFAULT 1,
            sync_time         TEXT DEFAULT '09:00',
            lc_import_status  TEXT DEFAULT '',
            lc_import_offset  INTEGER DEFAULT 0,
            lc_import_has_more INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id           SERIAL PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            platform     TEXT NOT NULL,
            problem_name TEXT,
            problem_id   TEXT,
            problem_url  TEXT DEFAULT '',
            difficulty   TEXT DEFAULT '',
            tags         TEXT DEFAULT '',
            solved_date  TEXT,
            UNIQUE(user_id, platform, problem_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sub_user     ON submissions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sub_platform ON submissions(user_id, platform);
        CREATE TABLE IF NOT EXISTS follows (
            follower_id  INTEGER NOT NULL,
            following_id INTEGER NOT NULL,
            created_at   TEXT DEFAULT '',
            PRIMARY KEY (follower_id, following_id)
        );
        CREATE TABLE IF NOT EXISTS admin_logs (
            id          SERIAL PRIMARY KEY,
            admin_id    INTEGER,
            action      TEXT,
            target_user TEXT,
            details     TEXT,
            ip          TEXT,
            created_at  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS login_history (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER,
            ip         TEXT,
            user_agent TEXT,
            success    INTEGER,
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sync_logs (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER,
            status     TEXT,
            message    TEXT,
            source     TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS daily_challenges (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL,
            problem_name  TEXT,
            problem_url   TEXT,
            difficulty    TEXT,
            platform      TEXT,
            topic         TEXT,
            assigned_date TEXT,
            completed     INTEGER DEFAULT 0,
            assigned_by   INTEGER DEFAULT 0,
            UNIQUE(user_id, problem_url, assigned_date)
        );
        CREATE TABLE IF NOT EXISTS mentor_assignments (
            id            SERIAL PRIMARY KEY,
            admin_id      INTEGER,
            user_id       INTEGER,
            problem_name  TEXT,
            problem_url   TEXT,
            difficulty    TEXT,
            platform      TEXT,
            topic         TEXT,
            note          TEXT DEFAULT '',
            assigned_date TEXT,
            due_date      TEXT DEFAULT '',
            completed     INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS custom_problems (
            id          SERIAL PRIMARY KEY,
            created_by  INTEGER,
            title       TEXT,
            description TEXT,
            difficulty  TEXT,
            topic       TEXT,
            url         TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS email_tokens (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            token      TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT '',
            used       INTEGER DEFAULT 0
        );
        """)
        # Default admin — password comes from ADMIN_INIT_PASSWORD (set this
        # in your environment / Render dashboard before first deploy). No
        # hardcoded fallback: if it's not set, no default admin is created
        # and you create one yourself via database/seed.py or the DB directly.
        admin_password = os.environ.get("ADMIN_INIT_PASSWORD")
        if admin_password and not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute("""
                INSERT INTO users
                (username,email,password,is_admin,is_verified,status,
                 enabled_platforms,created_at)
                VALUES (?,?,?,1,1,'active',?,?)
            """, ("admin", "admin@dsatracker.local",
                  generate_password_hash(admin_password),
                  '["Codeforces","LeetCode","AtCoder"]',
                  datetime.now().isoformat()))
        db.commit()


def ensure_extension_schema():
    """Adds the extension_token column (Chrome-extension pairing) if it
    doesn't already exist, and an index for the token lookup on /save_cookie.
    Called unconditionally at import time from app.py (same reasoning as
    ensure_contest_schema()): ensure_db_columns() below only runs under the
    `python app.py` __main__ block, which never executes on Vercel, so any
    schema patch that production needs has to run here instead.
    """
    with get_db() as db:
        try:
            db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS extension_token TEXT")
        except Exception:
            pass
        try:
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_extension_token "
                "ON users(extension_token) WHERE extension_token IS NOT NULL"
            )
        except Exception:
            pass
        db.commit()


def ensure_year_schema():
    """
    PHASE 2 — year-wise architecture. Adds:
      * users.cohort_year (TEXT, nullable — NULL means "not yet
        assigned a year"; existing pre-Phase-2 students stay NULL
        rather than being silently guessed into a year/sheet)
      * year_sheets: the single source of truth mapping a year/cohort
        to the Google Spreadsheet ID that holds that year's students.
        Configured by the mentor via /admin/year_sheets. Nothing else
        in the app should hardcode a spreadsheet ID or read this table
        directly — always go through services/year_sheet_service.py.
    Called unconditionally at import time (same reasoning as
    ensure_extension_schema() above: this also has to run on Vercel,
    which never executes the `python app.py` __main__ block).
    """
    with get_db() as db:
        try:
            db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cohort_year TEXT")
        except Exception:
            pass
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS year_sheets (
                    year           TEXT PRIMARY KEY,
                    spreadsheet_id TEXT NOT NULL,
                    updated_at     TEXT
                )
            """)
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX IF NOT EXISTS idx_users_cohort_year ON users(cohort_year)")
        except Exception:
            pass
        db.commit()


def ensure_recommendation_schema():
    """
    PHASE 3 — mentor Recommendation + News system.

    ONE reusable `recommendations` table (no recommendations_2028-style
    per-year tables). Year isolation is a WHERE-clause concern
    (cohort_year column), not a schema/table concern — same pattern as
    users.cohort_year from Phase 2's ensure_year_schema().

    `category` is a free-text TEXT column, not an enum — this keeps the
    set of categories (News/Internship/Hackathon/...) a UI-level concern
    (routes/recommendations.py ships a suggested list) instead of a
    schema migration every time a mentor wants a new one.

    Called unconditionally at import time, same reasoning as
    ensure_year_schema()/ensure_extension_schema(): this also has to run
    on Vercel, which never executes the `python app.py` __main__ block.

    Formal migration counterpart: database/migrations/0008_recommendations.sql
    (same coexistence pattern as ensure_contest_schema() +
    0002_contest_tracker.sql/0006_contest_phase3_schema.sql — this
    function guarantees cold starts never 500 on a fresh DB; the
    migration file is the reviewable, ordered schema record for
    `python database/migrate.py`). Kept intentionally in sync — if you
    change one, change the other.
    """
    with get_db() as db:
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id           SERIAL PRIMARY KEY,
                    cohort_year  TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    description  TEXT DEFAULT '',
                    category     TEXT DEFAULT 'Announcement',
                    external_url TEXT DEFAULT '',
                    image_url    TEXT DEFAULT '',
                    created_by   INTEGER,
                    created_at   TEXT DEFAULT '',
                    updated_at   TEXT DEFAULT '',
                    published    INTEGER DEFAULT 1,
                    pinned       INTEGER DEFAULT 0
                )
            """)
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX IF NOT EXISTS idx_reco_cohort_year ON recommendations(cohort_year)")
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX IF NOT EXISTS idx_reco_cohort_published ON recommendations(cohort_year, published)")
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX IF NOT EXISTS idx_reco_category ON recommendations(category)")
        except Exception:
            pass
        db.commit()


def ensure_db_columns():
    """Legacy-DB column patcher: adds columns that init_db()'s CREATE TABLE
    IF NOT EXISTS won't add to an already-existing table. Moved verbatim
    from app.py, where it ran once on startup.

    PHASE 4 FIX: these statements didn't use "IF NOT EXISTS", and on any
    DB where init_db()'s baseline CREATE TABLE already includes
    lc_imported/auto_sync_enabled/sync_time (i.e. every DB created from
    the current schema — only genuinely pre-Phase-1 DBs need this patch),
    the very first ALTER TABLE in the loop raised a duplicate-column
    error that aborted the whole Postgres transaction. The bare
    except/pass caught the Python exception but never rolled back, so
    every statement after it (including full_name/reg_no/roll_no/branch
    below, added in this phase) silently failed too — the "aborted
    transaction" error was swallowed the same way. Each statement now
    gets its own connection/transaction so one already-applied column
    can't poison the rest."""
    stmts = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_imported INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_sync_enabled INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_time TEXT DEFAULT '09:00'",
        # full_name/reg_no/roll_no/branch are read/written throughout
        # routes/auth.py (signup), routes/admin.py (student profile edit),
        # and routes/recommendations.py (mentor_name join) but were never
        # added by any migration — signup and the recommendations feed
        # both 500'd on a fresh/current DB because these columns simply
        # didn't exist yet.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS reg_no TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS roll_no TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS branch TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_import_status TEXT DEFAULT ''",
        # Chunked /import_leetcode continuation state (see routes/sync.py +
        # sync/chunked_import.py). lc_import_offset is the LeetCode
        # submissions-API offset to resume from on the NEXT button press;
        # lc_import_has_more tracks whether the first full import has
        # finished walking all pages yet. Reused as-is across requests —
        # never reset to 0 except by a genuinely fresh/first import.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_import_offset INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_import_has_more INTEGER DEFAULT 1",
        # One-click "Import LC" orchestrator (see routes/sync.py,
        # sync/chunked_import.py, assets/js/main.js importLC()) drives
        # /import_codeforces -> /import_atcoder -> /import_leetcode (chunked)
        # as three+ separate requests. cf_imported/ac_imported are the same
        # "has this platform's single-shot import ever succeeded for this
        # user" flag lc_imported already tracked for LeetCode.
        # initial_import_completed is the persisted AND of all three — set
        # server-side only once cf_imported AND ac_imported AND lc_imported
        # are all 1, never on a partial run. GET /dashboard already does
        # SELECT * FROM users, so this is exposed to the frontend with no
        # extra route needed; it's the single source of truth the frontend
        # uses to decide whether to show the Import LC button, and it
        # survives refresh/logout/login/new device since it's DB state, not
        # localStorage.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cf_imported INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ac_imported INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS initial_import_completed INTEGER DEFAULT 0",
        # Pre-existing gap found while testing the new import endpoints
        # against a freshly-provisioned DB: bot_sheet_sync.py's INSERT
        # (and the identical INSERT in the new sync/chunked_import.py,
        # which intentionally mirrors it) has always written a
        # submission_url column that no CREATE TABLE / migration file
        # ever added — only submitted_at (migration 0005) was. On a DB
        # created from db.py's own baseline schema this INSERT would
        # already fail with UndefinedColumn, for the OLD /import_lc too,
        # not just the new endpoints. Additive, IF NOT EXISTS, no data
        # loss — same self-healing pattern as every other line here.
        "ALTER TABLE submissions ADD COLUMN IF NOT EXISTS submission_url TEXT DEFAULT ''",
        # Same gap as submission_url above, for the same reason:
        # submitted_at was only ever added via migrations/0005 (the
        # standalone migrate.py script), never via this unconditional
        # boot-time patcher — so a DB that only ever ran app.py's startup
        # (e.g. every Vercel cold start) and never database/migrate.py
        # would still be missing it, and the same INSERT would 500.
        "ALTER TABLE submissions ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ",
        # Auto-sync dashboard popup: distinguishes a cron/scheduler-triggered
        # sync (source='auto') from a student's own "Sync Now" click or an
        # admin-triggered sync, so the frontend only pops up a notification
        # for the ones the user didn't personally initiate. See
        # scheduler.py::_log_auto_sync / services/scheduler_service.py and
        # routes/dashboard.py's last_sync_msg.
        "ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'",
        # Journey/progress report feature: the date a student's DSA journey
        # officially "starts" from (student-chosen, defaults to their
        # signup date if never set — see services/progress_report_service.py).
        # Stored as a real DATE, not TEXT, since it's only ever set through
        # our own date picker and never needs the legacy multi-format
        # tolerance solved_date has.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS journey_start_date DATE",
        # Real DATE mirror of submissions.solved_date (see indexes.sql's
        # long-standing note: solved_date is free-form TEXT in either
        # DD-MM-YYYY (normal_sync.py's production sync path) or YYYY-MM-DD
        # (bot_sheet_sync.py / sync/*.py / services/incremental_sync/),
        # so BETWEEN/ORDER BY on it sorts lexicographically and silently
        # mixes formats. solved_on is the real, unambiguous DATE the new
        # progress report (and any future date-range query) should filter
        # on instead. Backfilled + kept in sync by
        # ensure_solved_on_backfilled() below; every INSERT into
        # submissions now also writes it going forward.
        "ALTER TABLE submissions ADD COLUMN IF NOT EXISTS solved_on DATE",
    ]
    for stmt in stmts:
        try:
            with get_db() as db:
                db.execute(stmt)
                db.commit()
        except Exception as e:
            print(f"[ensure_db_columns] statement failed, continuing: {stmt!r}: {e}")


def ensure_solved_on_backfilled():
    """One-time (but idempotent/self-healing, like everything else in this
    file) backfill of submissions.solved_on from the legacy free-form
    solved_date TEXT column, for every row that doesn't have it yet. Safe
    to run on every cold start: the WHERE solved_on IS NULL guard means a
    fully-backfilled DB does a cheap no-op scan, not a rewrite.

    Handles the two formats that actually occur in this DB (see the note
    on solved_date above): 'DD-MM-YYYY' and 'YYYY-MM-DD'. Rows whose
    solved_date doesn't match either pattern (or is NULL/blank) are left
    with solved_on = NULL rather than guessed at — callers that need a
    date should treat NULL solved_on as "unknown", not "missing data".
    """
    try:
        with get_db() as db:
            db.execute("""
                UPDATE submissions
                SET solved_on = CASE
                    WHEN solved_date ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN solved_date::date
                    WHEN solved_date ~ '^\\d{2}-\\d{2}-\\d{4}$' THEN to_date(solved_date, 'DD-MM-YYYY')
                    WHEN solved_date ~ '^\\d{2}/\\d{2}/\\d{4}$' THEN to_date(solved_date, 'DD/MM/YYYY')
                    ELSE NULL
                END
                WHERE solved_on IS NULL AND COALESCE(solved_date,'') != ''
            """)
            db.commit()
    except Exception as e:
        print(f"[ensure_solved_on_backfilled] backfill failed, continuing: {e}")

    try:
        with get_db() as db:
            db.execute("CREATE INDEX IF NOT EXISTS idx_sub_user_solved_on ON submissions(user_id, solved_on)")
            db.commit()
    except Exception as e:
        print(f"[ensure_solved_on_backfilled] index creation failed, continuing: {e}")

    try:
        with get_db() as db:
            db.execute("""
                UPDATE users
                SET journey_start_date = substring(created_at from 1 for 10)::date
                WHERE journey_start_date IS NULL
                  AND created_at ~ '^\\d{4}-\\d{2}-\\d{2}'
            """)
            db.commit()
    except Exception as e:
        print(f"[ensure_solved_on_backfilled] journey_start_date default failed, continuing: {e}")
