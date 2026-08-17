"""
routes/reports.py — the weekly report page (with 5-week history and an
admin all-users view), its CSV export, and the endpoint that kicks off
bot_sheet_sync.py in the background. Moved verbatim from app.py.
"""

import io
import csv
from datetime import datetime, timedelta

from flask import request, jsonify, send_file
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from workers.report_worker import run_weekly_report_for_user
from services.progress_report_service import build_journey_report, set_journey_start_date


@app.route("/weekly_report")
@login_required
@verified_required
def weekly_report():

    start_param = request.args.get("start")
    # Admin can view all users combined OR a specific user
    view_all    = request.args.get("view") == "all" and current_user.is_admin
    target_uid  = request.args.get("uid")  # admin viewing a specific user

    if start_param:
        start = datetime.strptime(start_param, "%Y-%m-%d")
    else:
        start = datetime.now() - timedelta(days=6)

    end = start + timedelta(days=6)
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")

    # Determine which user(s) to query
    if view_all:
        # Admin: all active users combined
        query_uid  = None
        report_label = "All Users"
    elif target_uid and current_user.is_admin:
        query_uid  = int(target_uid)
        with get_db() as db:
            u = db.execute("SELECT username FROM users WHERE id=%s", (query_uid,)).fetchone()
        report_label = u["username"] if u else "Unknown"
    else:
        query_uid  = current_user.id
        report_label = current_user.username

    with get_db() as db:
        if view_all:
            subs = db.execute("""
                SELECT s.*, u.username FROM submissions s
                JOIN users u ON s.user_id = u.id
                WHERE s.solved_date BETWEEN ? AND ?
                AND u.status='active' AND u.is_admin=0
            """, (start_str, end_str)).fetchall()
        else:
            subs = db.execute("""
                SELECT * FROM submissions
                WHERE user_id=? AND solved_date BETWEEN ? AND ?
            """, (query_uid, start_str, end_str)).fetchall()

    # ── Per-user breakdown for admin all-view ──────────────────────────────
    user_breakdown = {}
    if view_all:
        for s in subs:
            uname = s["username"]
            if uname not in user_breakdown:
                user_breakdown[uname] = {"solved": 0, "cf": 0, "lc": 0, "ac": 0}
            user_breakdown[uname]["solved"] += 1
            p = s["platform"]
            if p == "Codeforces":   user_breakdown[uname]["cf"] += 1
            elif p == "LeetCode":   user_breakdown[uname]["lc"] += 1
            elif p == "AtCoder":    user_breakdown[uname]["ac"] += 1
        # sort by solved desc
        user_breakdown = dict(sorted(user_breakdown.items(),
                                     key=lambda x: x[1]["solved"], reverse=True))

    # ── Main calculations ──────────────────────────────────────────────────
    solved_count = len(subs)
    cf = lc = ac = 0
    topic_count = {}
    days = set()

    for s in subs:
        p = s["platform"]
        if p == "Codeforces":   cf += 1
        elif p == "LeetCode":   lc += 1
        elif p == "AtCoder":    ac += 1

        tags  = s["tags"] or ""
        topic = tags.split(";")[0] if tags else "General"
        topic_count[topic] = topic_count.get(topic, 0) + 1
        days.add(s["solved_date"])

    if len(topic_count) >= 2:
        top_topic  = max(topic_count, key=topic_count.get)
        weak_topic = min(topic_count, key=topic_count.get)
        # Ensure strong != weak (happens when all topics have equal count)
        if top_topic == weak_topic:
            weak_topic = "Insufficient data"
    elif len(topic_count) == 1:
        top_topic  = list(topic_count.keys())[0]
        weak_topic = "Insufficient data"
    else:
        top_topic  = "N/A"
        weak_topic = "N/A"
    streak     = len(days)

    report = {
        "solved_count": solved_count,
        "cf_count":     cf,
        "lc_count":     lc,
        "ac_count":     ac,
        "top_topic":    top_topic,
        "weak_topic":   weak_topic,
        "streak":       streak,
        "week_start":   start_str,
        "label":        report_label
    }

    # ── History (last 5 weeks) ─────────────────────────────────────────────
    history = []
    with get_db() as db:
        for i in range(1, 6):
            s = start - timedelta(days=7 * i)
            e = s + timedelta(days=6)
            s_str = s.strftime("%Y-%m-%d")
            e_str = e.strftime("%Y-%m-%d")

            if view_all:
                rows = db.execute("""
                    SELECT s.* FROM submissions s
                    JOIN users u ON s.user_id=u.id
                    WHERE s.solved_date BETWEEN ? AND ?
                    AND u.status='active' AND u.is_admin=0
                """, (s_str, e_str)).fetchall()
            else:
                rows = db.execute("""
                    SELECT * FROM submissions
                    WHERE user_id=? AND solved_date BETWEEN ? AND ?
                """, (query_uid, s_str, e_str)).fetchall()

            if not rows:
                continue

            h_cf = h_lc = h_ac = 0
            for r in rows:
                if r["platform"] == "Codeforces":   h_cf += 1
                elif r["platform"] == "LeetCode":   h_lc += 1
                elif r["platform"] == "AtCoder":    h_ac += 1

            history.append({
                "week_start":   s_str,
                "solved_count": len(rows),
                "cf_count":     h_cf,
                "lc_count":     h_lc,
                "ac_count":     h_ac
            })

    # Admin: list of all users for dropdown
    all_users = []
    if current_user.is_admin:
        with get_db() as db:
            all_users = db.execute(
                "SELECT id, username FROM users WHERE status='active' AND is_admin=0 ORDER BY username"
            ).fetchall()

    return jsonify({
        "report": report,
        "history": history,
        "user": {"id": current_user.id, "username": current_user.username, "is_admin": bool(current_user.is_admin)},
        "view_all": view_all,
        "user_breakdown": user_breakdown,
        "all_users": [dict(u) for u in all_users],
        "target_uid": target_uid
    })


@app.route("/weekly_csv")
@login_required
def weekly_csv():
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)

    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    with get_db() as db:
        subs = db.execute("""
            SELECT solved_date,problem_name,problem_url,difficulty,platform,tags
            FROM submissions
            WHERE user_id=? AND solved_date BETWEEN ? AND ?
        """, (current_user.id, start_str, end_str)).fetchall()

    out = io.StringIO()
    w = csv.writer(out)

    w.writerow(["DATE", "NAME", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC"])

    for s in subs:
        topic = (s["tags"] or "").split(";")[0] if s["tags"] else "General"
        w.writerow([
            s["solved_date"],
            s["problem_name"],
            s["problem_url"],
            s["difficulty"],
            s["platform"],
            topic
        ])

    out.seek(0)

    return send_file(
        io.BytesIO(out.read().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="weekly_report.csv"
    )


@app.route("/api/weekly_report")
@login_required
def api_weekly_report():
    run_weekly_report_for_user(current_user.id)
    return {"status": "started"}


# ── Journey / Full Progress Report ──────────────────────────────────────────
# Week-by-week report from the student's chosen start date to the current
# week: total marks per week, a daily LeetCode done/not-done grid, and
# weekly Codeforces/AtCoder done/not-done flags. See
# services/progress_report_service.py for the actual computation — this
# route just resolves who to report on (self, or admin viewing a student)
# and hands off, same admin/self pattern as /weekly_report above.
@app.route("/api/journey_report")
@login_required
@verified_required
def api_journey_report():
    from datetime import datetime as _dt

    target_uid = request.args.get("uid")
    if target_uid and current_user.is_admin:
        try:
            query_uid = int(target_uid)
        except ValueError:
            return jsonify({"error": "Invalid uid"}), 400
    else:
        query_uid = current_user.id

    start_override = request.args.get("start")
    start_date = None
    if start_override:
        try:
            start_date = _dt.strptime(start_override, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "start must be YYYY-MM-DD"}), 400

    report = build_journey_report(query_uid, start_date=start_date)
    if "error" in report:
        return jsonify(report), 404

    # Admin: list of all users for the picker (mirrors /weekly_report)
    all_users = []
    if current_user.is_admin:
        with get_db() as db:
            all_users = db.execute(
                "SELECT id, username FROM users WHERE status='active' AND is_admin=0 ORDER BY username"
            ).fetchall()

    with get_db() as db:
        target_user = db.execute(
            "SELECT id, username, journey_start_date FROM users WHERE id=?", (query_uid,)
        ).fetchone()

    report["is_own_report"] = (query_uid == current_user.id)
    report["target_user"] = dict(target_user) if target_user else None
    report["all_users"] = [dict(u) for u in all_users]
    return jsonify(report)


@app.route("/api/journey_start", methods=["POST"])
@login_required
@verified_required
def api_set_journey_start():
    """Lets a student (or an admin, on a student's behalf via ?uid=) pick
    the start date their journey/progress report should count weeks from."""
    data = request.get_json() or {}
    start = (data.get("start") or "").strip()
    target_uid = data.get("uid")

    if target_uid and current_user.is_admin:
        try:
            query_uid = int(target_uid)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid uid"}), 400
    else:
        query_uid = current_user.id

    try:
        saved = set_journey_start_date(query_uid, start)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400

    return jsonify({"success": True, "journey_start_date": saved})
