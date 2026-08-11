"""
routes/dashboard.py — the main user dashboard, and the two "mark complete"
actions on it (daily challenge, mentor assignment). Moved verbatim from
app.py.

PART 5: GET /dashboard now returns the exact same data as a JSON object
instead of rendering dashboard.html — the frontend's dashboard.html
(static, on Cloudflare Pages) fetches this and renders it with JS. No
query or computation changed, only Row objects -> plain dicts (see
rows_to_dicts in utils/helpers.py) so jsonify() can serialize them.
"""

from flask import jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from utils.helpers import get_counts, rows_to_dicts
from services.sync_engine import get_dashboard_data, generate_daily_challenges


def _is_solved_by_student_db(db, user_id, problem_url, problem_name):
    """Check submissions table to see if a student has solved the problem."""
    if problem_url and problem_url.strip():
        row = db.execute(
            "SELECT id FROM submissions WHERE user_id=? AND problem_url=? LIMIT 1",
            (user_id, problem_url.strip())
        ).fetchone()
        if row:
            return True
    if problem_name and problem_name.strip():
        row = db.execute(
            "SELECT id FROM submissions WHERE user_id=? AND LOWER(problem_name)=LOWER(?) LIMIT 1",
            (user_id, problem_name.strip())
        ).fetchone()
        if row:
            return True
    return False


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
@verified_required
def dashboard():
    with get_db() as db:
        subs = db.execute(
            "SELECT * FROM submissions WHERE user_id=? ORDER BY solved_date DESC",
            (current_user.id,)
        ).fetchall()

        fol_count = db.execute(
            "SELECT COUNT(*) as c FROM follows WHERE follower_id=?",
            (current_user.id,)
        ).fetchone()["c"]

        fwg_count = db.execute(
            "SELECT COUNT(*) as c FROM follows WHERE following_id=?",
            (current_user.id,)
        ).fetchone()["c"]

        sync_log = db.execute(
            "SELECT * FROM sync_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (current_user.id,)
        ).fetchone()

    # MAIN DATA
    data = get_dashboard_data(subs)

    # ADD COUNTS
    total, total_solved = get_counts(current_user.id)
    data["total"] = total
    data["solved"] = total_solved

    # EXTRA DATA
    data["following_count"] = fol_count
    data["follower_count"] = fwg_count
    data["last_sync_msg"] = dict(sync_log) if sync_log else None
    if isinstance(data.get("recent"), list):
        data["recent"] = [dict(r) if hasattr(r, "keys") else r for r in data["recent"]]

    # Daily challenges
    challenges = generate_daily_challenges(current_user.id, get_db)

    # Mentor tasks — auto-refresh completed status from actual submissions
    with get_db() as db:
        all_tasks = db.execute(
            "SELECT * FROM mentor_assignments WHERE user_id=?",
            (current_user.id,)
        ).fetchall()
        # Auto-complete any assignment the student has already solved
        for t in all_tasks:
            if not t["completed"]:
                is_solved = _is_solved_by_student_db(db, current_user.id,
                                                      t["problem_url"], t["problem_name"])
                if is_solved:
                    db.execute(
                        "UPDATE mentor_assignments SET completed=1 WHERE id=? AND user_id=?",
                        (t["id"], current_user.id)
                    )
        db.commit()
        # Re-fetch after auto-refresh so frontend gets correct state
        mentor_tasks = db.execute(
            "SELECT * FROM mentor_assignments WHERE user_id=? ORDER BY assigned_date DESC",
            (current_user.id,)
        ).fetchall()

    # Sheet URL
    sheet_url = None
    if current_user.sheet_id and current_user.sheet_id.strip():
        sheet_url = f"https://docs.google.com/spreadsheets/d/{current_user.sheet_id}"

    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id=?",
            (current_user.id,)
        ).fetchone()

    user_dict = dict(user) if user else {}
    user_dict.pop("password", None)
    user_dict.pop("lc_password", None)
    user_dict.pop("lc_session_cookie", None)
    user_dict.pop("lc_csrf_token", None)

    return jsonify({
        "user": user_dict,
        "data": data,
        "challenges": rows_to_dicts(challenges) if challenges and hasattr(challenges[0], "keys") else (challenges or []),
        "mentor_tasks": rows_to_dicts(mentor_tasks),
        "sheet_url": sheet_url
    })


# ── Mark challenge complete ────────────────────────────────────────────────────
@app.route("/challenge/complete/<int:cid>", methods=["POST"])
@login_required
@verified_required
def complete_challenge(cid):
    with get_db() as db:
        db.execute(
            "UPDATE daily_challenges SET completed=1 WHERE id=? AND user_id=?",
            (cid, current_user.id)
        )
        db.commit()
    return jsonify({"success": True})


@app.route("/mentor/complete/<int:mid>", methods=["POST"])
@login_required
@verified_required
def complete_mentor(mid):
    with get_db() as db:
        # Security: only allow student to mark their OWN assignment complete
        task = db.execute(
            "SELECT id, completed FROM mentor_assignments WHERE id=? AND user_id=?",
            (mid, current_user.id)
        ).fetchone()
        if not task:
            return jsonify({"success": False, "message": "Assignment not found."}), 404
        if task["completed"]:
            return jsonify({"success": True, "message": "Already completed."})
        db.execute(
            "UPDATE mentor_assignments SET completed=1 WHERE id=? AND user_id=?",
            (mid, current_user.id)
        )
        db.commit()
    return jsonify({"success": True})
