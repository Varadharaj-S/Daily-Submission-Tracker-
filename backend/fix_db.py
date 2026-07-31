"""
fix_db.py — Run this once to add new columns to your existing PostgreSQL database.
Usage: python fix_db.py
"""
from database.db import get_db

with get_db() as conn:
    rows = conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'users'
    """).fetchall()
    columns = [r["column_name"] for r in rows]

    added = []

    def add_column(name, ddl):
        if name not in columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {ddl}")
            added.append(name)

    add_column("lc_session_cookie", "lc_session_cookie TEXT DEFAULT ''")
    add_column("lc_csrf_token", "lc_csrf_token TEXT DEFAULT ''")
    add_column("lc_password", "lc_password TEXT DEFAULT ''")
    add_column("enabled_platforms", "enabled_platforms TEXT DEFAULT '[\"Codeforces\",\"LeetCode\",\"AtCoder\"]'")
    add_column("lc_imported", "lc_imported INTEGER DEFAULT 0")
    add_column("auto_sync_enabled", "auto_sync_enabled INTEGER DEFAULT 1")
    add_column("sync_time", "sync_time TEXT DEFAULT '09:00'")

    conn.commit()

if added:
    print(f"✅ Added columns: {', '.join(added)}")
else:
    print("⚡ All columns already exist — nothing to do.")
