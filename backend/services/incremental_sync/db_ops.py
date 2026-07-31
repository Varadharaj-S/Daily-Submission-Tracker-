"""
services/incremental_sync/db_ops.py — reads/writes the new
last_cf_sync/last_lc_sync/last_ac_sync columns (migration 0004), and
inserts submissions with accurate "was this actually new" tracking.

user_id stays the only link to submissions, same as today — reg_no is
never used as a key here, only for display/Sheet-matching elsewhere
(contest_sheet.py). PostgreSQL remains the single source of truth; the
Sheet is a read-only-feeling report that only this module (and
contest_sheet.py, for the separate contest tracker) ever writes to.
"""

from datetime import datetime

from database.db import get_db

PLATFORM_SYNC_COLUMN = {
    "Codeforces": "last_cf_sync",
    "LeetCode": "last_lc_sync",
    "AtCoder": "last_ac_sync",
}


def get_last_sync_times(user_id):
    """Returns {'Codeforces': datetime|None, 'LeetCode': ..., 'AtCoder': ...}"""
    with get_db() as db:
        row = db.execute(
            "SELECT last_cf_sync, last_lc_sync, last_ac_sync FROM users WHERE id=?",
            (user_id,)
        ).fetchone()
    if not row:
        return {p: None for p in PLATFORM_SYNC_COLUMN}
    return {
        "Codeforces": row["last_cf_sync"],
        "LeetCode": row["last_lc_sync"],
        "AtCoder": row["last_ac_sync"],
    }


def set_last_sync(user_id, platform, when=None):
    col = PLATFORM_SYNC_COLUMN.get(platform)
    if not col:
        raise ValueError(f"unknown platform: {platform}")
    when = when or datetime.now()
    with get_db() as db:
        db.execute(f"UPDATE users SET {col}=? WHERE id=?", (when, user_id))
        db.commit()


def insert_new_submissions(user_id, items):
    """
    Inserts each item (dict from a fetcher) with ON CONFLICT DO NOTHING,
    keyed the same way the rest of the app already does
    (user_id, platform, problem_id). Uses RETURNING id rather than
    checking cursor.rowcount per statement in a loop, so "was this row
    actually new" is determined the same way regardless of DB driver
    quirks.

    Returns only the items that were genuinely new (i.e. actually
    inserted) — duplicates from re-fetching an overlapping window are
    silently dropped here, same guarantee production sync already has,
    just made explicit and testable in this module.
    """
    if not items:
        return []

    newly_inserted = []
    with get_db() as db:
        for item in items:
            row = db.execute("""
                INSERT INTO submissions
                (user_id, platform, problem_id, problem_name, problem_url, difficulty, tags, solved_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, platform, problem_id) DO NOTHING
                RETURNING id
            """, (
                user_id, item["platform"], item["problem_id"], item["problem_name"],
                item["problem_url"], item["difficulty"], item["tags"], item["solved_date"]
            )).fetchone()
            if row is not None:
                newly_inserted.append(item)
        db.commit()
    return newly_inserted
