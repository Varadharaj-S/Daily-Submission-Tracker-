"""
services/progress_report_service.py — the "journey" / full progress report:
given a student's start date, builds one card per week from that date up to
the current (possibly partial) week, each with:

  - total marks (problems solved) that week
  - a 7-day grid of whether LeetCode was solved EACH day (daily requirement)
  - whether Codeforces was solved at least once that week (weekly requirement)
  - whether AtCoder was solved at least once that week (weekly requirement)
  - an overall week status: "Done" / "Not Done" / "In Progress"

This is intentionally a SEPARATE module from routes/reports.py's existing
single-week `/weekly_report` (kept as-is for backward compatibility) — this
answers "how has this student been doing since day 1", not just "this week".

IMPORTANT: this reads submissions.solved_on (a real DATE column), NOT the
legacy solved_date TEXT column that /weekly_report's raw BETWEEN query used.
solved_date is free-form text written in two different formats depending on
which sync path wrote it (DD-MM-YYYY vs YYYY-MM-DD — see the note in
database/indexes.sql and database/db.py's ensure_solved_on_backfilled()), so
comparing it lexicographically silently drops/misdates rows across month
boundaries. solved_on is backfilled + kept in sync at every INSERT site
specifically so this kind of date-range report can be correct.
"""

from datetime import date, datetime, timedelta

from database.db import get_db

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_date(value):
    """Accepts a date/datetime object or 'YYYY-MM-DD' string, returns a
    date object (or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def resolve_start_date(user_row):
    """Resolves the date a student's journey report should start counting
    from: users.journey_start_date if set, else their signup date
    (created_at), else — for a genuinely old row with neither — the date of
    their very first submission, else falls back to today (so the report
    is always at least renderable, just as a single, mostly-empty week)."""
    d = _parse_date(user_row.get("journey_start_date"))
    if d:
        return d
    d = _parse_date(user_row.get("created_at"))
    if d:
        return d
    return None  # caller falls back to earliest submission / today


def set_journey_start_date(user_id, start_date_str):
    """Validates + persists a student's chosen journey start date. Returns
    the normalized 'YYYY-MM-DD' string on success, raises ValueError on a
    bad/future date."""
    d = _parse_date(start_date_str)
    if not d:
        raise ValueError("Invalid date — expected YYYY-MM-DD.")
    if d > date.today():
        raise ValueError("Start date can't be in the future.")
    with get_db() as db:
        db.execute("UPDATE users SET journey_start_date=? WHERE id=?", (d.isoformat(), user_id))
        db.commit()
    return d.isoformat()


def build_journey_report(user_id, start_date=None, today=None):
    """Builds the full week-by-week journey report for one user.

    start_date: date object to start Week 1 from. If None, resolved from
                the user's own journey_start_date/created_at, and if
                neither exists, from their earliest submission.
    today:      date object for "now" (injectable for tests); defaults to
                date.today().

    Returns a dict: {start_date, weeks: [...], summary: {...}} or
    {"error": "..."} if the user doesn't exist.
    """
    today = today or date.today()

    with get_db() as db:
        user = db.execute(
            "SELECT id, username, journey_start_date, created_at FROM users WHERE id=?",
            (user_id,)
        ).fetchone()
    if not user:
        return {"error": "User not found"}

    if start_date is None:
        start_date = resolve_start_date(user)

    if start_date is None:
        with get_db() as db:
            row = db.execute(
                "SELECT MIN(solved_on) AS earliest FROM submissions WHERE user_id=? AND solved_on IS NOT NULL",
                (user_id,)
            ).fetchone()
        start_date = _parse_date(row and row.get("earliest")) or today

    if start_date > today:
        start_date = today

    # Pull every dated submission from start_date to today in ONE query,
    # then bucket in Python — far cheaper than one query per week for a
    # journey that can span many months.
    with get_db() as db:
        rows = db.execute("""
            SELECT platform, solved_on
            FROM submissions
            WHERE user_id=? AND solved_on BETWEEN ? AND ?
        """, (user_id, start_date.isoformat(), today.isoformat())).fetchall()

    # by_day[date] = {"LeetCode": n, "Codeforces": n, "AtCoder": n}
    by_day = {}
    for r in rows:
        d = _parse_date(r["solved_on"])
        if not d:
            continue
        bucket = by_day.setdefault(d, {"LeetCode": 0, "Codeforces": 0, "AtCoder": 0})
        platform = r["platform"] or ""
        if platform in bucket:
            bucket[platform] += 1
        else:
            bucket.setdefault("Other", 0)
            bucket["Other"] += 1

    weeks = []
    week_start = start_date
    week_index = 0
    lc_streak = 0
    lc_best_streak = 0
    total_solved_journey = 0

    while week_start <= today:
        week_index += 1
        week_end = min(week_start + timedelta(days=6), today + timedelta(days=365))  # cap unused
        actual_week_end = week_start + timedelta(days=6)

        days = []
        lc_count = cf_count = ac_count = other_count = 0
        elapsed_days = 0
        lc_done_elapsed = 0

        for i in range(7):
            d = week_start + timedelta(days=i)
            is_future = d > today
            bucket = by_day.get(d, {"LeetCode": 0, "Codeforces": 0, "AtCoder": 0})
            lc_solved_today = bucket.get("LeetCode", 0) > 0

            lc_count += bucket.get("LeetCode", 0)
            cf_count += bucket.get("Codeforces", 0)
            ac_count += bucket.get("AtCoder", 0)
            other_count += bucket.get("Other", 0)

            if not is_future:
                elapsed_days += 1
                if lc_solved_today:
                    lc_done_elapsed += 1
                    lc_streak += 1
                    lc_best_streak = max(lc_best_streak, lc_streak)
                else:
                    lc_streak = 0

            days.append({
                "date": d.isoformat(),
                "label": DAY_LABELS[d.weekday()],
                "leetcode_done": lc_solved_today,
                "problems_solved": sum(bucket.get(k, 0) for k in ("LeetCode", "Codeforces", "AtCoder", "Other")),
                "is_future": is_future,
                "is_today": d == today,
            })

        total_solved = lc_count + cf_count + ac_count + other_count
        total_solved_journey += total_solved

        cf_done = cf_count > 0
        ac_done = ac_count > 0
        lc_all_done = elapsed_days > 0 and lc_done_elapsed == elapsed_days
        is_current_week = actual_week_end >= today >= week_start

        if is_current_week:
            # Week isn't over yet — never call it "Not Done" prematurely.
            status = "Done" if (lc_all_done and cf_done and ac_done) else "In Progress"
        else:
            status = "Done" if (lc_all_done and cf_done and ac_done) else "Not Done"

        weeks.append({
            "week_index": week_index,
            "week_start": week_start.isoformat(),
            "week_end": actual_week_end.isoformat(),
            "is_current_week": is_current_week,
            "days": days,
            "leetcode_days_done": lc_done_elapsed,
            "leetcode_days_elapsed": elapsed_days,
            "leetcode_all_done": lc_all_done,
            "codeforces_done": cf_done,
            "codeforces_count": cf_count,
            "atcoder_done": ac_done,
            "atcoder_count": ac_count,
            "leetcode_count": lc_count,
            "total_solved": total_solved,
            "status": status,
        })

        week_start = week_start + timedelta(days=7)

    weeks_done = sum(1 for w in weeks if w["status"] == "Done")
    weeks_not_done = sum(1 for w in weeks if w["status"] == "Not Done")
    weeks_in_progress = sum(1 for w in weeks if w["status"] == "In Progress")

    summary = {
        "username": user["username"],
        "start_date": start_date.isoformat(),
        "today": today.isoformat(),
        "total_weeks": len(weeks),
        "weeks_done": weeks_done,
        "weeks_not_done": weeks_not_done,
        "weeks_in_progress": weeks_in_progress,
        "total_solved": total_solved_journey,
        "leetcode_best_streak": lc_best_streak,
        "leetcode_current_streak": lc_streak,
    }

    return {"start_date": start_date.isoformat(), "weeks": weeks, "summary": summary}
