"""
contest/contest_utils.py — small stateless helpers shared across the
contest module. Phase 1 scope: just status computation and code
normalization. Platform-specific parsing helpers land here in Phase 3.
"""

import re
from datetime import datetime, date, time as time_cls, timedelta

# Admins enter contest_date/start_time/end_time as IST wall-clock (matching
# what students see on the judge's own contest page). submissions.submitted_at
# is stored via datetime.fromtimestamp(epoch) (normal_sync.py / bot_sheet_sync.py),
# which lands in the SERVER's local time — UTC on Render/Vercel. Without this
# offset, get_contest_window() compared a naive IST range against naive UTC
# timestamps 5:30 apart, so every submission fell "outside" the window and
# every student graded as ABS despite real, in-window solves sitting in the DB.
IST_OFFSET = timedelta(hours=5, minutes=30)


def compute_status(contest_date, start_time, end_time, now=None):
    """
    Returns 'Upcoming' | 'Running' | 'Completed' given a contest's date and
    start/end times. Computed fresh on every read rather than trusted from
    the stored column, the same pattern services/tracker_service.py uses
    for daily_tracker_sheet_results — status always reflects the current
    time, never goes stale waiting for a scheduler run (Phase 3).
    """
    now = now or datetime.now()

    if isinstance(contest_date, str):
        contest_date = datetime.strptime(contest_date, "%Y-%m-%d").date()
    if isinstance(start_time, str):
        start_time = datetime.strptime(start_time, "%H:%M").time()
    if isinstance(end_time, str):
        end_time = datetime.strptime(end_time, "%H:%M").time()

    start_dt = datetime.combine(contest_date, start_time)
    end_dt = datetime.combine(contest_date, end_time)

    if now < start_dt:
        return "Upcoming"
    if start_dt <= now <= end_dt:
        return "Running"
    return "Completed"


def normalize_contest_code(code):
    """Contest codes are used as Google Sheet column headers (Phase 2) and
    as a unique key, so keep them short and predictable: alnum + dash/underscore."""
    code = re.sub(r"[^A-Za-z0-9_-]", "", str(code or "").strip())
    return code[:32]


def get_contest_window(contest):
    """Returns (start_datetime, end_datetime) for a contest dict.

    contest_sync.py's module docstring has always referenced this function
    ("contest_utils.get_contest_window") as the thing that computes the
    grading window — it was never actually written, which is one of
    several reasons sync silently could never work. Accepts either raw
    psycopg2 date/time objects or the string form contest_service.py's
    _serialize_row() produces (both show up depending on which caller
    handed the dict over), same tolerant pattern as compute_status() above.

    Returned as UTC-equivalent naive datetimes (admin-entered IST minus
    IST_OFFSET) so this lines up with submissions.submitted_at, which is
    stored via datetime.fromtimestamp(epoch) in the server's local time —
    UTC on Render/Vercel (see IST_OFFSET comment at top of file). Without
    this shift, every submission looked "outside" the contest window and
    everyone graded as ABS even with real, in-window solves already in
    the DB. compute_status() above is untouched — it compares against
    datetime.now() on this same server for UI status, not against
    submitted_at, so it isn't affected by this mismatch.
    """
    contest_date = contest["contest_date"]
    start_time = contest["start_time"]
    end_time = contest["end_time"]

    if isinstance(contest_date, str):
        contest_date = datetime.strptime(contest_date, "%Y-%m-%d").date()
    if isinstance(start_time, str):
        start_time = datetime.strptime(start_time, "%H:%M").time()
    if isinstance(end_time, str):
        end_time = datetime.strptime(end_time, "%H:%M").time()

    start_dt = datetime.combine(contest_date, start_time) - IST_OFFSET
    end_dt = datetime.combine(contest_date, end_time) - IST_OFFSET
    return start_dt, end_dt
