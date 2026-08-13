"""
utils/helpers.py — small, stateless helpers used by several route modules.
Moved verbatim from app.py.
"""

import os
import threading
from datetime import datetime

from flask_login import current_user

from database.db import get_db


def run_background(target, *args, **kwargs):
    """
    Fire-and-forget helper: runs `target` in a daemon thread so the HTTP
    response is returned immediately.

    IMPORTANT — Vercel note:
    The old version of this helper ran `target` SYNCHRONOUSLY when the VERCEL
    env var was detected, under the assumption that a daemon thread wouldn't
    finish before the serverless process was frozen. That caused the 504:
    /import_lc held the Vercel request open for the entire LeetCode import
    (5–15 minutes) and Vercel killed it after ~10 s.

    The correct fix for long-running work on Vercel is to delegate it to the
    Render persistent backend — NOT to run it synchronously inside the Vercel
    request. /import_lc now does that delegation in routes/sync.py and never
    calls run_background at all on Vercel.

    This function is now always a thread. Any caller that needs to do real work
    on Vercel must use the Render delegation pattern (routes/internal.py)
    instead of calling run_background.
    """
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


def rows_to_dicts(rows):
    """PART 5 (frontend/backend split): Jinja could iterate DB Row objects
    directly; jsonify() can't serialize them, so every route that used to
    pass rows into render_template() now converts them with this first.
    No data is added, removed, or transformed — just Row -> dict."""
    return [dict(r) for r in rows] if rows else []


def get_counts(user_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(DISTINCT problem_id) AS cnt
            FROM submissions
            WHERE user_id=?
        """, (user_id,)).fetchone()
    total = (row["cnt"] if row else 0) or 0

    # solved = all rows (already solved submissions)
    return total, total


def _parse_any_date(value):
    """Parse dates stored in multiple formats used by the app."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    fmts = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%y")
    for fmt in fmts:
        try:
            return datetime.strptime(s[:10], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _date_label(dt_obj, fallback="Unknown"):
    if not dt_obj:
        return fallback
    return dt_obj.strftime("%d-%m-%Y")


def _clean_topic(topic):
    return " ".join(str(topic or "").split()).strip()


def _group_rows(rows, include_username=False):
    """Return grouped daily rows from submission rows."""
    groups = {}
    for row in rows:
        dt = _parse_any_date(row["solved_date"])
        date_label = _date_label(dt, str(row["solved_date"] or "Unknown"))

        username = row["username"] if include_username and "username" in row.keys() else current_user.username
        key = (date_label, username)

        if key not in groups:
            groups[key] = {
                "date": date_label,
                "date_dt": dt or datetime.min,
                "username": username,
                "solved_count": 0,
                "easy": 0,
                "medium": 0,
                "hard": 0,
                "problems": []
            }

        item = groups[key]
        item["solved_count"] += 1
        diff = str(row["difficulty"] or "").strip().lower()
        if diff == "easy":
            item["easy"] += 1
        elif diff == "medium":
            item["medium"] += 1
        elif diff == "hard":
            item["hard"] += 1

        item["problems"].append({
            "name": row["problem_name"] or "",
            "url": row["problem_url"] or "",
            "difficulty": row["difficulty"] or "",
            "platform": row["platform"] or "",
            "topic": row["tags"] or row["topic"] if "topic" in row.keys() else (row["tags"] or "")
        })

    # Sort: latest date first, then count desc, then username
    ordered = sorted(
        groups.values(),
        key=lambda x: (
            x["date_dt"] if x["date_dt"] != datetime.min else datetime.min,
            x["solved_count"],
            x["username"].lower()
        ),
        reverse=True
    )
    return ordered


def _get_user_by_username(username):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return row
