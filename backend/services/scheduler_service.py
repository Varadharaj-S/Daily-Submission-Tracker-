"""
services/scheduler_service.py — per-user auto-sync scheduling.
Moved verbatim from app.py.

`run_scheduler()` is the loop app.py used to start in a background thread
on boot; `schedule_user()` is a per-user variant that isn't currently
wired to any caller in the original app.py either (it was dead code there
too) but is preserved as-is rather than dropped during the move.
"""

import time
from datetime import datetime, timedelta

from database.db import get_db
from services.sync_engine import sync_user_data


def schedule_user(u):
    while True:
        now = datetime.now()

        sync_time = u.get("sync_time")
        if not sync_time:
            return

        target_time = datetime.strptime(sync_time, "%H:%M").replace(
            year=now.year,
            month=now.month,
            day=now.day
        )

        if target_time <= now:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()

        print(f"⏳ {u['username']} waiting {int(wait_seconds)} sec")

        time.sleep(wait_seconds)

        print(f"⏰ Running auto sync for {u['username']}")
        sync_user_data(u, get_db)
        time.sleep(60)


def run_scheduler():
    while True:
        now = datetime.now().strftime("%H:%M")

        with get_db() as db:
            users = db.execute(
                "SELECT * FROM users WHERE lc_imported=1 AND COALESCE(auto_sync_enabled,1)=1"
            ).fetchall()

        for u in users:
            u = dict(u)

            last_run = u.get("last_sync", "")

            if last_run:
                last_run = last_run[-5:]  # HH:MM only

            if u.get("sync_time") == now and last_run != now:
                print(f"⏰ Running auto sync for {u['username']}")

                sync_user_data(u, get_db)

                with get_db() as db:
                    db.execute(
                        "UPDATE users SET last_sync=? WHERE id=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M"), u["id"])
                    )
                    db.commit()

        time.sleep(60)  # THIS MUST ALIGN WITH while
