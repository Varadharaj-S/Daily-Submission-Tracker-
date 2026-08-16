"""
contest_scheduler.py — Render Cron entry point for contest sync.

render.yaml's "dsa-tracker-contest-sync" cron job runs this file every
5 minutes (`python contest_scheduler.py`) — that job was pointing here
before this file existed, so it crashed on every tick and no contest
ever got auto-synced.

This is a thin one-shot wrapper: each cron tick calls run_due_contests()
once and exits (Render's cron model runs a fresh process per tick, it
does not keep a script alive) — same underlying function that
services/scheduler_service.py's run_contest_scheduler() calls in its
in-process loop as a fallback for deployments with no cron job set up.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))


# NOTE: this used to also write GOOGLE_SERVICE_JSON out to a
# google_creds.json file next to this script. Nothing reads that file
# back — contest.contest_sync (like every other Sheets call in this repo)
# parses GOOGLE_SERVICE_JSON directly from the env var. On Vercel,
# /var/task is read-only, so the write always failed and only added noise
# to the function logs. Removed.


def main():
    start = datetime.now()
    print(f"[contest_scheduler] tick start {start.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        from contest.contest_sync import run_due_contests
        results = run_due_contests()
        ok = sum(1 for _, success, _ in results if success)
        fail = len(results) - ok
        print(f"[contest_scheduler] done — {ok} synced, {fail} failed, {len(results)} due contest(s) checked.")
    except Exception as e:
        print(f"[contest_scheduler] tick failed: {e}")


if __name__ == "__main__":
    main()
