"""
fake_db.py — in-memory SQLite-backed replacement for database.db.get_db().
Used in offline tests so we don't need PostgreSQL.

The schema only defines the columns that contest_sheet.import_students() queries:
    SELECT id, username, full_name, reg_no, roll_no, branch
    FROM users
    WHERE status='active' AND is_admin=0 AND cohort_year=?
    ORDER BY username ASC
"""

import sqlite3
import contextlib

_DB_PATH = ":memory:"
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT DEFAULT '',
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                full_name TEXT DEFAULT '',
                reg_no TEXT DEFAULT '',
                roll_no TEXT DEFAULT '',
                branch TEXT DEFAULT '',
                cohort_year TEXT DEFAULT NULL,
                enabled_platforms TEXT DEFAULT '[]',
                created_at TEXT DEFAULT ''
            )
        """)
        _conn.commit()
    return _conn


class _FakeCursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        rows = self._cur.fetchall()
        return [dict(r) for r in rows]

    @property
    def rowcount(self):
        return self._cur.rowcount


class _FakeConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return _FakeCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        return False


def get_db():
    return _FakeConn(_get_conn())


def reset_db():
    """Drop and recreate users table — used between test groups."""
    conn = _get_conn()
    conn.execute("DELETE FROM users")
    conn.commit()
