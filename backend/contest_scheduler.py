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


def _write_google_creds():
    """Write GOOGLE_SERVICE_JSON env var to file if not already on disk
    (contest sync writes results back to the roster/contest sheet)."""
    creds_json = os.environ.get("GOOGLE_SERVICE_JSON", "")
    creds_path = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if creds_json and not os.path.exists(creds_path):
        try:
            with open(creds_path, "w") as f:
                f.write(creds_json)
        except Exception as e:
            print(f"[contest_scheduler] Could not write google_creds.json: {e}")


def main():
    start = datetime.now()
    print(f"[contest_scheduler] tick start {start.strftime('%Y-%m-%d %H:%M:%S')}")
    _write_google_creds()
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
