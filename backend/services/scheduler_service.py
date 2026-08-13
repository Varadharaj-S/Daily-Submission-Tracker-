"""
services/scheduler_service.py — in-process per-user daily auto-sync loop.

PHASE 4 FIX: this file used to contain the contest-sync loop instead of
this one (the two got swapped/overwritten during the Phase 3 merge — its
own docstring even said "services/contest_scheduler_service.py"). That
left `workers/sync_worker.py`'s `from services.scheduler_service import
run_scheduler` import failing at startup (ImportError), which crashed
the whole app before a single route could load. The contest-sync loop
now lives at services/contest_scheduler_service.py (its correct home,
per its own docstring), and this file is restored to what
workers/sync_worker.py's docstring describes: "the same
threading.Thread(daemon=True) approach app.py used at the bottom of the
file" for periodic per-user sync via normal_sync.sync_user_data().

This is a *safety net*, not the primary mechanism — most deployments
should sync via the student-triggered /sync route or a real cron job.
This loop just makes sure active users' data doesn't go stale if nobody
manually hits "Sync now" for a while.
"""

import time

from config import Config

# How often (in hours) this loop sweeps all active users and re-syncs
# their CF/LC/AC data. Configurable via env; defaults to once a day.
TICK_SECONDS = int(getattr(Config, "AUTO_SYNC_INTERVAL_HOURS", 24)) * 3600


def run_scheduler():
    """Background loop: periodically re-syncs every active, verified user
    with at least one platform handle configured. Never trusts a
    client-provided year/user — iterates every active row from the DB,
    same as the rest of the app treats cohort_year as server-truth."""
    from database.db import get_db
    from normal_sync import sync_user_data

    # Wait one full tick before the first run, same reasoning as
    # contest_scheduler_service.py: don't immediately re-fire a sync
    # attempt against a failing user on every dev-server reloader restart.
    time.sleep(TICK_SECONDS)

    while True:
        try:
            with get_db() as db:
                users = db.execute("""
                    SELECT * FROM users
                    WHERE status='active' AND is_admin=0
                      AND (
                        COALESCE(cf_handle,'') != '' OR
                        COALESCE(lc_handle,'') != '' OR
                        COALESCE(ac_handle,'') != ''
                      )
                """).fetchall()

            for row in users:
                user = dict(row)
                try:
                    sync_user_data(user, get_db)
                except Exception as e:
                    print(f"[scheduler_service] sync failed for user {user.get('username')}: {e}")
        except Exception as e:
            print(f"[scheduler_service] tick failed: {e}")

        time.sleep(TICK_SECONDS)
