"""
routes/contest.py — Phase 1 of the Contest Tracker: the /student_contest
dashboard, contest create/edit/delete, and /contest/history. No Google
Sheets integration (Phase 2) and no platform sync (Phase 3) yet — status
is computed live from date/time (see contest.contest_utils.compute_status),
not driven by a scheduler, since there isn't one yet.

Access note: the design doc asks for "Admin and Mentor" access, but this
codebase doesn't actually have a separate mentor role — "Mentor Mode"
(routes/admin.py's /admin/mentor) is itself an admin-only page. Contest
routes are gated the same way, behind admin_required, until/unless a real
mentor role gets added.
"""

from datetime import datetime

from flask import request, jsonify
from flask_login import current_user

from extensions import app
from utils.decorators import login_required, admin_required, log_admin
from utils.security import sanitize
from contest import contest_service
from contest import contest_sheet


@app.route("/student_contest")
@login_required
@admin_required
def student_contest():
    contests = contest_service.list_contests()
    counts = {"Upcoming": 0, "Running": 0, "Completed": 0}
    for c in contests:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return jsonify({
        "contests": contests,
        "counts": counts,
        "total_contests": len(contests),
    })


@app.route("/contest/create", methods=["GET", "POST"])
@login_required
@admin_required
def contest_create():
    if request.method == "POST":
        name = sanitize(request.form.get("contest_name", ""), 120)
        code = sanitize(request.form.get("contest_code", ""), 32)
        platform = request.form.get("platform", "Codeforces")
        contest_date = request.form.get("contest_date", "")
        start_time = request.form.get("start_time", "")
        end_time = request.form.get("end_time", "")

        if not (name and code and contest_date and start_time and end_time):
            return jsonify({"success": False, "message": "All fields are required."}), 400

        if platform not in ("Codeforces", "LeetCode", "AtCoder"):
            return jsonify({"success": False, "message": "Invalid platform."}), 400

        try:
            datetime.strptime(contest_date, "%Y-%m-%d")
            datetime.strptime(start_time, "%H:%M")
            datetime.strptime(end_time, "%H:%M")
        except ValueError:
            return jsonify({"success": False, "message": "Invalid date or time format."}), 400

        if end_time <= start_time:
            return jsonify({"success": False, "message": "End time must be after start time."}), 400

        row, error = contest_service.create_contest(
            name, code, platform, contest_date, start_time, end_time, current_user.id
        )
        if error:
            return jsonify({"success": False, "message": error}), 400

        log_admin("create_contest", target=code, details=name)

        sheet_ok, sheet_msg = contest_sheet.ensure_sheet_for_contest(dict(row))
        if sheet_ok:
            return jsonify({"success": True, "message": f"Contest '{name}' created. {sheet_msg}", "redirect": "/contest_dashboard.html"})
        else:
            # Contest exists in Postgres either way — Sheets failure is
            # surfaced but non-fatal, same pattern as email failures
            # elsewhere in this app not blocking signup.
            return jsonify({
                "success": True,
                "message": f"Contest '{name}' created, but {sheet_msg} "
                           f"Use 'Refresh Sheet' on the dashboard once fixed.",
                "level": "warning",
                "redirect": "/contest_dashboard.html"
            })

    return jsonify({"ok": True})


@app.route("/contest/edit/<int:cid>", methods=["POST"])
@login_required
@admin_required
def contest_edit(cid):
    c = contest_service.get_contest(cid)
    if not c:
        return jsonify({"success": False, "message": "Contest not found."}), 404

    contest_service.update_contest(
        cid,
        contest_name=sanitize(request.form.get("contest_name", ""), 120) or None,
        platform=request.form.get("platform") or None,
        contest_date=request.form.get("contest_date") or None,
        start_time=request.form.get("start_time") or None,
        end_time=request.form.get("end_time") or None,
    )
    contest_service.sync_status_column(cid)
    log_admin("edit_contest", target=c["contest_code"])
    return jsonify({"success": True, "message": "Contest updated."})


@app.route("/contest/delete/<int:cid>", methods=["POST"])
@login_required
@admin_required
def contest_delete(cid):
    c = contest_service.get_contest(cid)
    if c:
        contest_service.delete_contest(cid)
        log_admin("delete_contest", target=c["contest_code"])
        return jsonify({"success": True, "message": f"Contest '{c['contest_name']}' deleted."})
    return jsonify({"success": False, "message": "Contest not found."}), 404


@app.route("/contest/history")
@login_required
@admin_required
def contest_history():
    contests = contest_service.list_contests(status="Completed")
    return jsonify({"contests": contests})


@app.route("/contest/refresh_sheet", methods=["POST"])
@login_required
@admin_required
def contest_refresh_sheet():
    ok, msg = contest_sheet.refresh_sheet()
    log_admin("refresh_contest_sheet", details=msg)
    return jsonify({"success": ok, "message": msg})
