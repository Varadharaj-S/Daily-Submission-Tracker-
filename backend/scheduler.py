"""
scheduler.py — Daily sync runner for Render Cron Job.

Render Cron command:  python scheduler.py

This script:
  1. Connects to the same DB as the Flask app (SQLite locally, PostgreSQL on Render).
  2. Fetches all active, verified users.
  3. For each user, runs sync_user_data() which pulls CF/LC/AC submissions.
  4. Marks cookie_expiry=1 if LeetCode returns 401/403.
  5. Logs results.

Environment variables (same as Flask app):
  DATABASE_URL  — PostgreSQL connection string (required)
  GOOGLE_SERVICE_JSON — service account JSON string (for Sheets sync)
"""

import os
import sys
import time
import json
from datetime import datetime

# ── DB helpers — shared PostgreSQL module ─────────────────────────────────────
from database.db import get_db
from utils.sync_schedule import now_ist


# ── Google Service Account (env var only) ──────────────────────────────────
# NOTE: this used to also try to write GOOGLE_SERVICE_JSON out to a
# google_creds.json file next to this script "for local/file-based auth".
# Nothing ever reads that file back — every Sheets call in this codebase
# (services/year_sheet_service.py, sync/sync_service.py, sheet_protect.py,
# bot_sheet_sync.py, bot.py, lock_master_sheet.py) parses
# GOOGLE_SERVICE_JSON directly from the env var with json.loads(). On
# Vercel, /var/task is read-only, so that write always failed and spammed
# "[scheduler] Could not write google_creds.json: [Errno 30] Read-only
# file system" into the function logs on every cron tick — cosmetic noise
# that looked like a real failure but never affected the actual sync.
# Removed rather than fixed (e.g. writing to /tmp instead), since the file
# was dead weight to begin with.


# ── Sync per-user ─────────────────────────────────────────────────────────────

def sync_one_user(user: dict, db_factory) -> dict:
    """
    Run full sync for a single user. Returns a result dict.
    Catches PermissionError (cookie expired) and marks the DB.
    """
    uid      = user["id"]
    username = user.get("username", f"user_{uid}")

    try:
        from normal_sync import sync_user_data
        result = sync_user_data(dict(user), db_factory)
        return {"uid": uid, "username": username, "ok": True, "result": result}
    except PermissionError as e:
        print(f"[scheduler] ⚠️  {username}: cookie expired ({e})")
        try:
            with db_factory() as db:
                db.execute("UPDATE users SET cookie_expiry=1 WHERE id=?", (uid,))
                db.commit()
        except Exception as dbe:
            print(f"[scheduler] DB update failed: {dbe}")
        return {"uid": uid, "username": username, "ok": False, "error": "cookie_expired"}
    except Exception as e:
        print(f"[scheduler] ❌  {username}: {e}")
        return {"uid": uid, "username": username, "ok": False, "error": str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

# How many users to sync in parallel. Sequential (1 user at a time + 1s
# sleep) doesn't scale: at 1000 users that's 1000+ seconds of sleep() alone,
# before any actual CF/LC/AC/Sheets API time — easily 30-60+ minutes total,
# which risks the cron job's execution-time budget and just delays syncing
# for users near the end of the list. Bounded concurrency fixes this without
# hammering the LC/CF/AC APIs or Neon's connection pool — each worker opens
# its own DB connection via get_db() (see database/db.py: every call opens a
# fresh psycopg2 connection, so this is safe across threads), and 10
# concurrent users is gentle enough not to trip per-IP rate limits on the
# competitive-programming platforms.
SYNC_CONCURRENCY = int(os.environ.get("SYNC_CONCURRENCY", "10"))

# How many users ONE Vercel serverless invocation processes before returning.
# No Render worker in this deployment (Cloudflare frontend + Vercel backend
# only), so there's no persistent process to hand the full sync off to —
# every invocation of /api/cron/daily-sync runs inside a Vercel function,
# hard-capped at ~10s (Hobby) / 60s (Pro). Even with SYNC_CONCURRENCY=10,
# 1000 users can't finish in one request. So instead of "sync everyone",
# each call syncs one small batch (ordered by user id) and reports back a
# cursor + whether more remain — see sync_batch() below and
# routes/cron.py's cron_daily_sync(). frontend/worker.js's Cloudflare Cron
# Trigger calls this in a loop (Cloudflare Workers aren't bound by Vercel's
# per-invocation limit) until every active user is synced for the day.
DAILY_SYNC_BATCH_SIZE = int(os.environ.get("DAILY_SYNC_BATCH_SIZE", "15"))


def sync_batch(after_id: int = 0, batch_size: int = None, concurrency: int = None) -> dict:
    """Sync ONE batch of users (id > after_id, ordered by id, LIMIT batch_size)
    and return immediately — built to run inside a single short-lived Vercel
    function call. The caller is responsible for looping: keep calling with
    `after_id = result["next_cursor"]` until `result["done"]` is True.
    """
    batch_size = batch_size or DAILY_SYNC_BATCH_SIZE
    concurrency = concurrency or SYNC_CONCURRENCY

    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM users
               WHERE status='active' AND is_verified=1 AND is_admin=0 AND id > ?
               ORDER BY id ASC LIMIT ?""",
            (after_id, batch_size),
        ).fetchall()

    all_users = [dict(u) if not isinstance(u, dict) else u for u in rows]
    results = {"ok": 0, "fail": 0, "cookie_expired": 0}

    # Sync every active user who hasn't been synced yet today (IST).
    # No sync_time window check — cron fires every 15 min, DB dedup
    # (ON CONFLICT DO NOTHING) prevents duplicate sheet rows.
    ref = now_ist()
    def _already_synced_today(u):
        last = u.get("last_sync")
        if not last:
            return False
        try:
            from utils.sync_schedule import _last_sync_ist
            dt = _last_sync_ist(last)
            return dt is not None and dt.date() == ref.date()
        except Exception:
            return False

    users = [u for u in all_users if not _already_synced_today(u)]

    if users:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from normal_sync import alfa_wakeup
        alfa_wakeup()  # one ping per batch, not per user
        with ThreadPoolExecutor(max_workers=min(concurrency, len(users))) as pool:
            futures = {pool.submit(sync_one_user, u, get_db): u["username"] for u in users}
            for future in as_completed(futures):
                username = futures[future]
                try:
                    res = future.result()
                except Exception as e:
                    print(f"  ❌ {username}: unexpected error: {e}")
                    results["fail"] += 1
                    continue

                if res["ok"]:
                    results["ok"] += 1
                    print(f"  ✅ {username}: synced.")
                elif res.get("error") == "cookie_expired":
                    results["cookie_expired"] += 1
                    print(f"  ⚠️  {username}: cookie expired.")
                else:
                    results["fail"] += 1
                    print(f"  ❌ {username}: {res.get('error', 'unknown')}")

    next_cursor = all_users[-1]["id"] if all_users else after_id
    done = len(all_users) < batch_size  # fewer rows than requested = reached the end

    return {
        "processed": len(users),
        "scanned": len(all_users),
        "next_cursor": next_cursor,
        "done": done,
        **results,
    }


def main():
    start = datetime.now()
    print(f"\n{'='*55}")
    print(f"  DSA Tracker — Daily Scheduler  [{start.strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*55}\n")

    # Fetch all active + verified users
    with get_db() as db:
        users = db.execute(
            "SELECT * FROM users WHERE status='active' AND is_verified=1 AND is_admin=0"
        ).fetchall()

    all_users = [dict(u) if not isinstance(u, dict) else u for u in users]
    ref = now_ist()
    def _already_synced_today(u):
        last = u.get("last_sync")
        if not last:
            return False
        try:
            from utils.sync_schedule import _last_sync_ist
            dt = _last_sync_ist(last)
            return dt is not None and dt.date() == ref.date()
        except Exception:
            return False
    users = [u for u in all_users if not _already_synced_today(u)]
    print(f"[scheduler] {len(all_users)} active user(s) scanned, "
          f"{len(users)} due for sync today (IST {ref.strftime('%H:%M')}) "
          f"(concurrency={SYNC_CONCURRENCY}).\n")

    results = {"ok": 0, "fail": 0, "cookie_expired": 0}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from normal_sync import alfa_wakeup
    if users:
        alfa_wakeup()  # one ping before the full batch, not per user

    with ThreadPoolExecutor(max_workers=SYNC_CONCURRENCY) as pool:
        futures = {
            pool.submit(sync_one_user, user, get_db): user.get("username", "?")
            for user in users
        }
        for future in as_completed(futures):
            username = futures[future]
            try:
                res = future.result()
            except Exception as e:
                # sync_one_user already catches its own exceptions and
                # returns a dict, but guard here too so one bad worker
                # can't kill the whole batch.
                print(f"  ❌ {username}: unexpected error: {e}")
                results["fail"] += 1
                continue

            if res["ok"]:
                results["ok"] += 1
                print(f"  ✅ {username}: synced.")
            elif res.get("error") == "cookie_expired":
                results["cookie_expired"] += 1
                print(f"  ⚠️  {username}: cookie expired — user must reconnect.")
            else:
                results["fail"] += 1
                print(f"  ❌ {username}: {res.get('error', 'unknown')}")

    elapsed = (datetime.now() - start).seconds
    print(f"\n{'='*55}")
    print(f"  ✅ Synced: {results['ok']}  "
          f"❌ Failed: {results['fail']}  "
          f"⚠️ Expired: {results['cookie_expired']}  "
          f"({elapsed}s)")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
