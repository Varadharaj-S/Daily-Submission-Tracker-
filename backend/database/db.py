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
            sync_time         TEXT DEFAULT '09:00'
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
        # Default admin
        if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute("""
                INSERT INTO users
                (username,email,password,is_admin,is_verified,status,
                 enabled_platforms,created_at)
                VALUES (?,?,?,1,1,'active',?,?)
            """, ("admin", "admin@dsatracker.local",
                  generate_password_hash("admin123"),
                  '["Codeforces","LeetCode","AtCoder"]',
                  datetime.now().isoformat()))
        db.commit()


def ensure_db_columns():
    """Legacy-DB column patcher: adds columns that init_db()'s CREATE TABLE
    IF NOT EXISTS won't add to an already-existing table. Moved verbatim
    from app.py, where it ran once on startup."""
    with get_db() as db:
        for stmt in [
            "ALTER TABLE users ADD COLUMN lc_imported INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN auto_sync_enabled INTEGER DEFAULT 1",
            "ALTER TABLE users ADD COLUMN sync_time TEXT DEFAULT '09:00'",
        ]:
            try:
                db.execute(stmt)
            except Exception:
                pass
        db.commit()
