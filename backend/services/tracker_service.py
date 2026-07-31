"""
services/tracker_service.py — the "Daily Tracker" feature: admin-defined
verification sheets that are auto-evaluated against each user's submission
history (weekly assignment, monthly goal, placement eligibility, topic
verification, attendance). Moved verbatim from app.py.
"""

import json
from datetime import datetime, timedelta

from database.db import get_db
from utils.helpers import _parse_any_date, _clean_topic


def _tracker_defaults():
    return [
        ("Daily Problems", "daily_problems", "Auto-filled from submissions / problem logs", 0, ""),
        ("Weekly Assignment", "weekly_assignment", "Auto-evaluated from the last 7 days", 10, ""),
        ("Contest Attendance", "contest_attendance", "Manual / admin verified contest sheet", 0, ""),
        ("Monthly Goal", "monthly_goal", "Auto-evaluated using current month submissions", 50, ""),
        ("Placement Eligibility", "placement_eligibility", "Auto-evaluated from total solved & hard problems", 300, "50"),
        ("Topic Verification", "topic_verification", "Auto-evaluated by topic count", 3, "Graphs"),
        ("Custom Sheets", "custom", "Admin-defined custom verification sheet", 0, "")
    ]


def ensure_tracker_schema():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS daily_tracker_sheets (
            id            SERIAL PRIMARY KEY,
            name          TEXT UNIQUE NOT NULL,
            sheet_type    TEXT NOT NULL DEFAULT 'custom',
            description   TEXT DEFAULT '',
            target_value  INTEGER DEFAULT 0,
            target_topic  TEXT DEFAULT '',
            created_by    INTEGER DEFAULT 0,
            visibility    TEXT DEFAULT 'private',
            created_at    TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS daily_tracker_sheet_results (
            sheet_id     INTEGER NOT NULL,
            student_id   INTEGER NOT NULL,
            status       TEXT DEFAULT '',
            solved_count INTEGER DEFAULT 0,
            details_json  TEXT DEFAULT '',
            remarks      TEXT DEFAULT '',
            updated_at   TEXT DEFAULT '',
            PRIMARY KEY (sheet_id, student_id)
        );
        """)
        # Seed default tracker sheets once
        for name, sheet_type, description, target_value, target_topic in _tracker_defaults():
            exists = db.execute(
                "SELECT 1 FROM daily_tracker_sheets WHERE name=?",
                (name,)
            ).fetchone()
            if not exists:
                db.execute("""
                    INSERT INTO daily_tracker_sheets
                    (name, sheet_type, description, target_value, target_topic, created_by, visibility, created_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    name, sheet_type, description, int(target_value or 0), str(target_topic or ""),
                    None, "private", datetime.now().isoformat(timespec="seconds")
                ))
        db.commit()


def _tracker_metrics_for_users():
    """
    Build per-user metrics from submissions so the tracker can compute
    weekly/monthly/topic/placement sheets.
    """
    today = datetime.now().date()
    week_start = today - timedelta(days=6)
    month_start = today.replace(day=1)

    with get_db() as db:
        rows = db.execute("""
            SELECT s.user_id, s.platform, s.problem_name, s.problem_id, s.problem_url,
                   s.difficulty, s.tags, s.solved_date, u.username
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE u.status='active' AND u.is_admin=0
            ORDER BY s.solved_date DESC, s.id DESC
        """).fetchall()

    metrics = {}
    for row in rows:
        uid = row["user_id"]
        if uid not in metrics:
            metrics[uid] = {
                "username": row["username"],
                "total": 0,
                "today": 0,
                "week": 0,
                "month": 0,
                "hard": 0,
                "topics": {},
                "rows": []
            }

        dt = _parse_any_date(row["solved_date"])
        if not dt:
            continue
        d = dt.date()
        topic = _clean_topic(row["tags"])
        difficulty = (row["difficulty"] or "").strip().lower()

        metrics[uid]["total"] += 1
        if d == today:
            metrics[uid]["today"] += 1
        if d >= week_start:
            metrics[uid]["week"] += 1
        if d >= month_start:
            metrics[uid]["month"] += 1
        if difficulty == "hard":
            metrics[uid]["hard"] += 1

        if topic:
            metrics[uid]["topics"][topic] = metrics[uid]["topics"].get(topic, 0) + 1

        metrics[uid]["rows"].append(row)

    return metrics


def refresh_tracker_sheet_results():
    """
    Auto-compute results for the built-in verification sheets.
    Manual contest/custom sheets can still be edited by admin.
    """
    metrics = _tracker_metrics_for_users()

    with get_db() as db:
        sheets = db.execute("SELECT * FROM daily_tracker_sheets ORDER BY id ASC").fetchall()
        active_users = db.execute("""
            SELECT id, username FROM users
            WHERE status='active' AND is_admin=0
            ORDER BY username ASC
        """).fetchall()

        for sheet in sheets:
            stype = (sheet["sheet_type"] or "").strip().lower()
            if stype not in {"weekly_assignment", "monthly_goal", "placement_eligibility", "topic_verification", "coding_attendance"}:
                continue

            target_value = int(sheet["target_value"] or 0)
            target_topic = str(sheet["target_topic"] or "").strip()

            for user in active_users:
                m = metrics.get(user["id"], {
                    "total": 0, "today": 0, "week": 0, "month": 0, "hard": 0, "topics": {}
                })

                status = "NO"
                solved_count = 0
                remarks = ""

                if stype == "weekly_assignment":
                    solved_count = int(m["week"])
                    status = "YES" if solved_count >= target_value else "NO"
                    remarks = f"Weekly solved: {solved_count}/{target_value}"
                elif stype == "monthly_goal":
                    solved_count = int(m["month"])
                    status = "YES" if solved_count >= target_value else "NO"
                    remarks = f"Monthly solved: {solved_count}/{target_value}"
                elif stype == "placement_eligibility":
                    solved_count = int(m["total"])
                    hard_target = int(target_topic) if str(target_topic).isdigit() else 50
                    hard_ok = int(m["hard"]) >= hard_target
                    solved_ok = solved_count >= (target_value or 300)
                    status = "YES" if (solved_ok and hard_ok) else "NO"
                    remarks = f"Total: {solved_count}, Hard: {m['hard']} (need {hard_target})"
                elif stype == "topic_verification":
                    solved_count = int(m["topics"].get(target_topic, 0))
                    status = "YES" if solved_count >= target_value else "NO"
                    remarks = f"{target_topic}: {solved_count}/{target_value}"
                elif stype == "coding_attendance":
                    solved_count = int(m["today"])
                    status = "Present" if solved_count >= 1 else "Absent"
                    remarks = f"Today's solved count: {solved_count}"

                db.execute("""
                    INSERT INTO daily_tracker_sheet_results
                    (sheet_id, student_id, status, solved_count, details_json, remarks, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sheet_id, student_id) DO UPDATE SET
                        status=excluded.status,
                        solved_count=excluded.solved_count,
                        details_json=excluded.details_json,
                        remarks=excluded.remarks,
                        updated_at=excluded.updated_at
                """, (
                    sheet["id"], user["id"], status, solved_count,
                    json.dumps({
                        "sheet_type": stype,
                        "target_value": target_value,
                        "target_topic": target_topic
                    }),
                    remarks,
                    datetime.now().isoformat(timespec="seconds")
                ))

        db.commit()
