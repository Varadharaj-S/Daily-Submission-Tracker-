from database.db import get_db

with get_db() as conn:
    rows = conn.execute("""
        SELECT id, username, lc_imported
        FROM users
    """).fetchall()

for row in rows:
    print(row)
