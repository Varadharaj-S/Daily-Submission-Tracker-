"""
database/migrate.py — Phase 1 migration runner for DSA Tracker v4.

What this does, in order:
  1. Connects using the same DATABASE_URL the app already uses (via db.py).
  2. Runs a pre-flight orphan check: for every foreign key this migration is
     about to add, checks whether any existing row points at a user_id/
     admin_id/etc that doesn't exist. If it finds any, it prints them and
     stops WITHOUT touching the schema — you fix the data, then re-run.
  3. Applies database/indexes.sql (new indexes on existing tables).
  4. Applies database/migrations/0001_phase1_schema_hardening.sql (foreign
     keys + new cache/log tables).

Everything is idempotent — re-running this after a successful run is a
no-op. Everything runs in a single transaction per file: if a file fails
partway through, it rolls back, it never leaves the schema half-migrated.

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/dsa_tracker"
    python database/migrate.py                # run it
    python database/migrate.py --dry-run       # only run the orphan check
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_db  # noqa: E402  (db.py lives right next to this file)

HERE = os.path.dirname(os.path.abspath(__file__))

# (description, query that returns orphan rows if any exist)
ORPHAN_CHECKS = [
    ("submissions.user_id -> users.id",
     "SELECT id, user_id FROM submissions s "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = s.user_id)"),

    ("follows.follower_id -> users.id",
     "SELECT follower_id, following_id FROM follows f "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = f.follower_id)"),

    ("follows.following_id -> users.id",
     "SELECT follower_id, following_id FROM follows f "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = f.following_id)"),

    ("admin_logs.admin_id -> users.id (non-null only)",
     "SELECT id, admin_id FROM admin_logs a "
     "WHERE admin_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = a.admin_id)"),

    ("login_history.user_id -> users.id (non-null only)",
     "SELECT id, user_id FROM login_history l "
     "WHERE user_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = l.user_id)"),

    ("sync_logs.user_id -> users.id (non-null only)",
     "SELECT id, user_id FROM sync_logs s "
     "WHERE user_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = s.user_id)"),

    ("daily_challenges.user_id -> users.id",
     "SELECT id, user_id FROM daily_challenges d "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = d.user_id)"),

    ("daily_challenges.assigned_by -> users.id (0 treated as NULL)",
     "SELECT id, assigned_by FROM daily_challenges d "
     "WHERE assigned_by IS NOT NULL AND assigned_by != 0 "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = d.assigned_by)"),

    ("mentor_assignments.user_id -> users.id",
     "SELECT id, user_id FROM mentor_assignments m "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = m.user_id)"),

    ("mentor_assignments.admin_id -> users.id (non-null only)",
     "SELECT id, admin_id FROM mentor_assignments m "
     "WHERE admin_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = m.admin_id)"),

    ("custom_problems.created_by -> users.id (non-null only)",
     "SELECT id, created_by FROM custom_problems c "
     "WHERE created_by IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = c.created_by)"),

    ("email_tokens.user_id -> users.id",
     "SELECT id, user_id FROM email_tokens e "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = e.user_id)"),

    ("daily_tracker_sheets.created_by -> users.id (0 treated as NULL)",
     "SELECT id, created_by FROM daily_tracker_sheets t "
     "WHERE created_by IS NOT NULL AND created_by != 0 "
     "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.created_by)"),

    ("daily_tracker_sheet_results.sheet_id -> daily_tracker_sheets.id",
     "SELECT sheet_id, student_id FROM daily_tracker_sheet_results r "
     "WHERE NOT EXISTS (SELECT 1 FROM daily_tracker_sheets t WHERE t.id = r.sheet_id)"),

    ("daily_tracker_sheet_results.student_id -> users.id",
     "SELECT sheet_id, student_id FROM daily_tracker_sheet_results r "
     "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = r.student_id)"),
]


def run_orphan_checks(db):
    print("Running pre-flight orphan checks...\n")
    found_any = False
    for description, query in ORPHAN_CHECKS:
        try:
            rows = db.execute(query).fetchall()
        except Exception as exc:
            # A missing table at this point (e.g. fresh install with no
            # daily_tracker_sheets yet) just means there's nothing to check.
            print(f"  [skip]  {description}  ({exc.__class__.__name__}, table likely not present yet)")
            continue
        if rows:
            found_any = True
            print(f"  [FAIL]  {description} — {len(rows)} orphaned row(s):")
            for r in rows[:10]:
                print(f"            {dict(r)}")
            if len(rows) > 10:
                print(f"            ... and {len(rows) - 10} more")
        else:
            print(f"  [ok]    {description}")
    print()
    return not found_any


def apply_sql_file(db, path):
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    print(f"Applying {os.path.relpath(path, HERE)} ...")
    db.executescript(sql)
    db.commit()
    print("  done.\n")


def main():
    dry_run = "--dry-run" in sys.argv

    db = get_db()
    try:
        clean = run_orphan_checks(db)
        if not clean:
            print("Aborting: fix or NULL out the orphaned rows above, then re-run.")
            print("(Nothing was changed — the orphan check runs before any DDL.)")
            sys.exit(1)

        if dry_run:
            print("Dry run only (--dry-run) — orphan check passed, no changes applied.")
            return

        apply_sql_file(db, os.path.join(HERE, "indexes.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0001_phase1_schema_hardening.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0002_contest_tracker.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0003_student_identity_fields.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0004_incremental_sync_timestamps.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0006_contest_phase3_schema.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0007_year_wise_architecture.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0008_recommendations.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0009_leetcode_import_cursor.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0010_initial_import_completed.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0011_solved_on_and_journey_start.sql"))
        apply_sql_file(db, os.path.join(HERE, "migrations", "0012_dedup_by_solved_date.sql"))

        print("Phase 1 migration complete.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
