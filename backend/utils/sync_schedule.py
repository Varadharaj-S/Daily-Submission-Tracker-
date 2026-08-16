"""
utils/sync_schedule.py — shared "is this user due for auto-sync right now?"
logic, used by both scheduler.py (Vercel/Cloudflare cron path) and
services/scheduler_service.py (thread-based fallback loop).

BUG THIS FIXES:
Previously, neither scheduler ever looked at users.sync_time or
users.auto_sync_enabled at all — scheduler.py synced every active+verified
user unconditionally whenever cron fired (twice a day, fixed UTC times),
and scheduler_service.py synced every user with a handle configured once
every AUTO_SYNC_INTERVAL_HOURS. So /set_sync_time correctly saved the
user's chosen time to the DB, but nothing ever read it back — the
"Auto Sync Time" setting had zero effect on when a sync actually ran.

FIX: cron now runs frequently (every 15 min — see frontend/wrangler.jsonc)
and, on each tick, only syncs users whose configured sync_time (interpreted
as IST, since that's this app's only userbase/timezone) has already passed
today AND who haven't been synced yet today. This is a "catch-up" style
check rather than an exact-minute match, so a slightly-late or missed cron
tick still fires the sync instead of skipping the day entirely.
"""

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def _parse_hhmm(sync_time: str) -> tuple[int, int]:
    try:
        hh_str, mm_str = (sync_time or "09:00").strip().split(":")[:2]
        hh, mm = int(hh_str), int(mm_str)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except Exception:
        pass
    return 9, 0  # fallback matches the users.sync_time DB default


def _last_sync_ist(last_sync: str):
    """Best-effort parse of the stored last_sync timestamp into IST.
    last_sync is written as an ISO UTC timestamp by sync_user_data(); this
    also tolerates a naive/no-tz string just in case."""
    if not last_sync:
        return None
    try:
        dt = datetime.fromisoformat(last_sync.strip())
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def is_due_for_auto_sync(user: dict, reference: datetime = None) -> bool:
    """True if `user` should be auto-synced right now.

    Conditions:
      - auto_sync_enabled is truthy
      - it is currently at or past the user's configured sync_time, IST,
        for today
      - the user hasn't already been successfully synced today
    """
    if not user.get("auto_sync_enabled", 1):
        return False

    ref = reference or now_ist()
    hh, mm = _parse_hhmm(user.get("sync_time"))
    scheduled_today = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if ref < scheduled_today:
        return False  # today's scheduled time hasn't arrived yet

    last_dt = _last_sync_ist(user.get("last_sync"))
    if last_dt is not None and last_dt.date() == ref.date() and last_dt >= scheduled_today:
        return False  # already synced today, at/after today's scheduled time

    return True
