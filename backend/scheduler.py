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


# ── Google Service Account (env var or file) ──────────────────────────────────

def _write_google_creds():
    """Write GOOGLE_SERVICE_JSON env var to file if not already on disk."""
    creds_json = os.environ.get("GOOGLE_SERVICE_JSON", "")
    creds_path = os.path.join(os.path.dirname(__file__), "google_creds.json")
    if creds_json and not os.path.exists(creds_path):
        try:
            with open(creds_path, "w") as f:
                f.write(creds_json)
            print("[scheduler] Wrote google_creds.json from env var.")
        except Exception as e:
            print(f"[scheduler] Could not write google_creds.json: {e}")
    return creds_path if os.path.exists(creds_path) else None


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
        # LeetCode cookie expired — mark in DB
        print(f"[scheduler] ⚠️  {username}: cookie expired ({e})")
        try:
            with db_factory() as db:
                db.execute(
                    "UPDATE users SET cookie_expiry=1 WHERE id=?", (uid,)
                )
                db.commit()
        except Exception as dbe:
            print(f"[scheduler] DB update failed: {dbe}")
        return {"uid": uid, "username": username, "ok": False, "error": "cookie_expired"}
    except Exception as e:
        print(f"[scheduler] ❌  {username}: {e}")
        return {"uid": uid, "username": username, "ok": False, "error": str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    print(f"\n{'='*55}")
    print(f"  DSA Tracker — Daily Scheduler  [{start.strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"{'='*55}\n")

    _write_google_creds()

    # Fetch all active + verified users
    with get_db() as db:
        users = db.execute(
            "SELECT * FROM users WHERE status='active' AND is_verified=1 AND is_admin=0"
        ).fetchall()

    print(f"[scheduler] Found {len(users)} active user(s) to sync.\n")

    results = {"ok": 0, "fail": 0, "cookie_expired": 0}

    for user in users:
        username = user.get("username", "?") if isinstance(user, dict) else user["username"]
        print(f"  ▶ Syncing {username}…")
        res = sync_one_user(dict(user) if not isinstance(user, dict) else user, get_db)

        if res["ok"]:
            results["ok"] += 1
            print(f"    ✅ Done.")
        elif res.get("error") == "cookie_expired":
            results["cookie_expired"] += 1
            print(f"    ⚠️  Cookie expired — user must reconnect.")
        else:
            results["fail"] += 1
            print(f"    ❌ Failed: {res.get('error', 'unknown')}")

        time.sleep(1)  # polite delay between users

    elapsed = (datetime.now() - start).seconds
    print(f"\n{'='*55}")
    print(f"  ✅ Synced: {results['ok']}  "
          f"❌ Failed: {results['fail']}  "
          f"⚠️ Expired: {results['cookie_expired']}  "
          f"({elapsed}s)")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
