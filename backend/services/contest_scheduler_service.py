"""
services/contest_scheduler_service.py — in-process contest-sync loop.

Same idea as services/scheduler_service.py's run_scheduler() (per-user daily
sync), but for contest results, and on a 5-minute cadence instead of daily.

This is a *safety net*, not the primary mechanism — contest_scheduler.py run
via Render Cron every 5 minutes is the reliable, dedicated-process way to
run this in production (see that file's docstring). This loop covers
deployments that only run the web service and never set up the cron job.
Both are safe to run at once: contest_sync.py's atomic per-contest claim
means only one of them ever actually processes a given contest, no matter
how many gunicorn workers or cron ticks overlap.
"""

import time

from config import Config

TICK_SECONDS = Config.CONTEST_SYNC_INTERVAL_MINUTES * 60


def run_contest_scheduler():
    from contest.contest_sync import run_due_contests

    # Wait one full tick before the first run — otherwise every dev-server
    # restart (Flask's reloader restarts on every file save) immediately
    # re-fires a sync attempt against whatever contest is currently
    # failing, which is what was flooding the console during testing.
    time.sleep(TICK_SECONDS)

    while True:
        try:
            run_due_contests()
        except Exception as e:
            print(f"[contest_scheduler_service] tick failed: {e}")
        time.sleep(TICK_SECONDS)
