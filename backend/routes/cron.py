"""
routes/cron.py — Vercel Cron entry point for the daily auto-sync job.
(PART 3 — deployment compatibility only, added file.)

On Render, the daily sync runs as its own cron service that executes
`python scheduler.py` directly (see render.yaml). Vercel's platform has no
equivalent "run this script on a schedule" primitive — Vercel Cron Jobs
work only by making an HTTP request to a path in the deployed app on a
schedule (configured in vercel.json). This file exists solely to give
Vercel Cron something to call. It does not duplicate, change, or
re-implement any sync logic — it just imports and calls the exact same
`scheduler.main()` function that `python scheduler.py` runs on Render, so
the two platforms use the same code path.

If CRON_SECRET is set in the environment, Vercel automatically sends it
as a Bearer token on every cron invocation, and requests without a
matching secret are rejected so this endpoint can't be used to trigger
syncs from outside:
https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs
If CRON_SECRET is not set, the endpoint runs unauthenticated (same as
before this file existed, since there was no such endpoint at all).
"""

import os

from flask import request, jsonify

from extensions import app


@app.route("/api/cron/daily-sync", methods=["GET", "POST"])
def cron_daily_sync():
    secret = os.environ.get("CRON_SECRET", "")
    if secret:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {secret}":
            return jsonify({"ok": False, "message": "Unauthorized"}), 401

    from scheduler import main as run_daily_sync
    run_daily_sync()

    return jsonify({"ok": True, "message": "Daily sync completed"})


@app.route("/api/cron/contest-sync", methods=["GET", "POST"])
def cron_contest_sync():
    """vercel.json's crons[] entry for '/api/cron/contest-sync' had no
    matching route — on Vercel that cron 404'd every tick and no contest
    was ever auto-synced there. Same pattern as cron_daily_sync() above:
    just calls the one real implementation (contest_scheduler.main(),
    which itself calls contest.contest_sync.run_due_contests())."""
    secret = os.environ.get("CRON_SECRET", "")
    if secret:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {secret}":
            return jsonify({"ok": False, "message": "Unauthorized"}), 401

    from contest_scheduler import main as run_contest_sync
    run_contest_sync()

    return jsonify({"ok": True, "message": "Contest sync completed"})
