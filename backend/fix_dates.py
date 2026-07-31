# fix_dates.py
from datetime import datetime
from database.db import get_db

with get_db() as conn:
    rows = conn.execute("SELECT id, solved_date FROM submissions").fetchall()

    for r in rows:
        old = r["solved_date"]
        try:
            new = datetime.strptime(old, "%d-%m-%Y").strftime("%Y-%m-%d")
            conn.execute("UPDATE submissions SET solved_date=? WHERE id=?", (new, r["id"]))
        except Exception:
            pass

    conn.commit()

print("✅ Dates fixed")
