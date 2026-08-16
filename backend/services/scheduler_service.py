"""
services/scheduler_service.py — in-process per-user auto-sync loop.

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

BUGFIX (per-user Auto Sync Time not triggering): this loop used to sweep
*every* user with a handle configured, once every AUTO_SYNC_INTERVAL_HOURS
(24h default) — it never looked at users.sync_time or
users.auto_sync_enabled at all, so a student's chosen "Auto Sync Time" in
Settings was saved to the DB by /set_sync_time but had no effect on
anything. It now ticks frequently (every TICK_SECONDS, default 15 min)
and each tick only syncs users whose configured sync_time has arrived for
today (IST) and who haven't been synced yet today — see
utils/sync_schedule.py for the shared due-check (also used by
scheduler.py, the Vercel/Cloudflare cron path).

This is a *safety net*, not the primary mechanism — most deployments
should sync via the student-triggered /sync route or a real cron job.
This loop just makes sure active users' data doesn't go stale if nobody
manually hits "Sync now" for a while.
"""

import time

from config import Config
from utils.sync_schedule import is_due_for_auto_sync, now_ist

# How often (in seconds) this loop wakes up and checks which users are due
# for their configured Auto Sync Time. Needs to be short relative to the
# HH:MM granularity users can pick, not once-a-day — a 24h tick could only
# ever line up with a user's chosen minute by coincidence. Configurable via
# env; defaults to every 15 minutes.
TICK_SECONDS = int(getattr(Config, "AUTO_SYNC_TICK_SECONDS", 900))


def run_scheduler():
    """Background loop: every TICK_SECONDS, syncs whichever active,
    verified users are currently due for their configured Auto Sync Time
    (and have auto_sync_enabled on). Never trusts a client-provided
    year/user — iterates every active row from the DB, same as the rest
    of the app treats cohort_year as server-truth."""
    from database.db import get_db
    from normal_sync import sync_user_data

    # Small initial delay so this doesn't immediately hammer the DB on
    # every dev-server reloader restart.
    time.sleep(min(TICK_SECONDS, 30))

    while True:
        try:
            with get_db() as db:
                rows = db.execute("""
                    SELECT * FROM users
                    WHERE status='active' AND is_admin=0
                      AND (
                        COALESCE(cf_handle,'') != '' OR
                        COALESCE(lc_handle,'') != '' OR
                        COALESCE(ac_handle,'') != ''
                      )
                """).fetchall()

            ref = now_ist()
            due_users = [dict(row) for row in rows if is_due_for_auto_sync(dict(row), ref)]

            for user in due_users:
                try:
                    sync_user_data(user, get_db)
                except Exception as e:
                    print(f"[scheduler_service] sync failed for user {user.get('username')}: {e}")
        except Exception as e:
            print(f"[scheduler_service] tick failed: {e}")

        time.sleep(TICK_SECONDS)
