"""
contest/contest_utils.py — small stateless helpers shared across the
contest module. Phase 1 scope: just status computation and code
normalization. Platform-specific parsing helpers land here in Phase 3.
"""

import re
from datetime import datetime, date, time as time_cls


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

    return datetime.combine(contest_date, start_time), datetime.combine(contest_date, end_time)
