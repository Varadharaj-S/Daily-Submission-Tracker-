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
    Deployment-compatibility helper (PART 3 / Vercel).

    Originally, routes fired background work with a bare
    `threading.Thread(target=..., daemon=True).start()` and returned
    immediately. That works on Render/local because the process stays
    alive between requests. On Vercel, a serverless function's process is
    frozen/torn down right after the HTTP response is sent, so a detached
    daemon thread is not guaranteed to finish its work.

    This helper preserves the exact original fire-and-forget behavior
    everywhere except Vercel: when the VERCEL env var is present (set
    automatically by the platform), the target is run synchronously
    in-process instead, so it actually completes before the response is
    returned. No business logic in `target` is changed either way.
    """
    if os.environ.get("VERCEL"):
        target(*args, **kwargs)
    else:
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
