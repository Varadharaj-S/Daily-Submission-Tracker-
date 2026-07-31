"""
services/incremental_sync/tests/test_db_ops.py — exercises db_ops.py
against a real Postgres DB. Run:

    export DATABASE_URL="postgresql://user:pass@host/db"
    python3 services/incremental_sync/tests/test_db_ops.py
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from database.db import get_db
from services.incremental_sync import db_ops


def _seed_user():
    with get_db() as db:
        db.execute("DELETE FROM users WHERE username='incsync_t'")
        db.execute("""
            INSERT INTO users (username,email,password,is_verified,status,enabled_platforms,created_at)
            VALUES ('incsync_t','i@t.com','x',1,'active','[]','2026-01-01')
        """)
        db.commit()
        row = db.execute("SELECT id FROM users WHERE username='incsync_t'").fetchone()
    return row["id"]


def _item(pid, name="Problem", platform="Codeforces"):
    return {
        "platform": platform, "problem_id": pid, "problem_name": name,
        "problem_url": f"https://x/{pid}", "difficulty": "Easy", "tags": "General",
        "solved_date": "13-07-2026",
    }


def run():
    uid = _seed_user()

    # 1. Initially, all last-sync timestamps are None
    times = db_ops.get_last_sync_times(uid)
    assert times == {"Codeforces": None, "LeetCode": None, "AtCoder": None}, times
    print("[ok] get_last_sync_times: all None for a brand-new user")

    # 2. set_last_sync updates only the given platform's column
    now = datetime.now()
    db_ops.set_last_sync(uid, "Codeforces", now)
    times = db_ops.get_last_sync_times(uid)
    assert times["Codeforces"] is not None
    assert times["LeetCode"] is None and times["AtCoder"] is None, \
        "setting one platform's sync time must not touch the others"
    print("[ok] set_last_sync: only touches the specified platform's column")

    # 3. insert_new_submissions: fresh items are all inserted and returned
    items = [_item("1-A"), _item("1-B"), _item("1-C")]
    inserted = db_ops.insert_new_submissions(uid, items)
    assert len(inserted) == 3, inserted
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) c FROM submissions WHERE user_id=?", (uid,)).fetchone()["c"]
    assert count == 3
    print("[ok] insert_new_submissions: all 3 fresh items inserted")

    # 4. Re-inserting the same items (simulating an overlapping incremental
    #    fetch window) must insert nothing and return an empty list
    inserted_again = db_ops.insert_new_submissions(uid, items)
    assert inserted_again == [], inserted_again
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) c FROM submissions WHERE user_id=?", (uid,)).fetchone()["c"]
    assert count == 3, "duplicate re-fetch must not create extra rows"
    print("[ok] insert_new_submissions: re-inserting duplicates is a true no-op, returns []")

    # 5. Mixed batch: 2 duplicates + 1 genuinely new -> only the new one comes back
    mixed = [_item("1-A"), _item("1-B"), _item("2-Z", name="New One")]
    inserted_mixed = db_ops.insert_new_submissions(uid, mixed)
    assert len(inserted_mixed) == 1 and inserted_mixed[0]["problem_id"] == "2-Z", inserted_mixed
    print("[ok] insert_new_submissions: mixed batch returns only the genuinely new item")

    with get_db() as db:
        db.execute("DELETE FROM users WHERE username='incsync_t'")
        db.commit()

    print("\nALL DB_OPS TESTS PASSED")


if __name__ == "__main__":
    run()
