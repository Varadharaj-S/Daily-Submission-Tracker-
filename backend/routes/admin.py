"""
routes/admin.py — the admin dashboard, user verify/delete/promote, mentor
mode, custom problems, per-user and all-user sync triggers, and DB
backup/restore. Moved verbatim from app.py, except backup/restore now call
into workers/backup_worker.py (same pg_dump/psql commands, extracted once
instead of duplicated across the two routes).
"""

import time
from datetime import datetime

from flask import request, jsonify

from extensions import app
from database.db import get_db
from utils.decorators import login_required, admin_required, log_admin
from utils.security import sanitize
from utils.helpers import run_background, rows_to_dicts
from flask_login import current_user
from services.sync_engine import sync_user_data
from workers.backup_worker import run_backup, run_restore, list_backups
from normal_sync import rebuild_user_sheet_from_db, backfill_missing_rows_from_db


# ── Admin Dashboard ────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():

    with get_db() as db:
        users   = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        counts  = {u["id"]: db.execute(
            "SELECT COUNT(*) as c FROM submissions WHERE user_id=?",
            (u["id"],)
        ).fetchone()["c"] for u in users}
        pending = db.execute(
            "SELECT COUNT(*) as c FROM users WHERE status='pending'"
        ).fetchone()["c"]
        total_subs = db.execute(
            "SELECT COUNT(*) as c FROM submissions"
        ).fetchone()["c"]
        logs = db.execute(
            "SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT 30"
        ).fetchall()

        # Analytics: signups per day (last 14 days)
        signups_raw = db.execute("""
            SELECT to_char(created_at::date, 'YYYY-MM-DD') as day, COUNT(*) as cnt
            FROM users WHERE created_at::date >= CURRENT_DATE - INTERVAL '14 days'
            GROUP BY created_at::date ORDER BY created_at::date
        """).fetchall()
        # Problems solved per day (last 14 days)
        probs_raw = db.execute("""
            SELECT solved_date, COUNT(*) as cnt
            FROM submissions
            GROUP BY solved_date
            ORDER BY solved_date DESC LIMIT 14
        """).fetchall()

        active_users = db.execute(
            "SELECT COUNT(*) as c FROM users WHERE status='active'"
        ).fetchone()["c"]

        custom_probs = db.execute(
            "SELECT * FROM custom_problems ORDER BY created_at DESC"
        ).fetchall()

    signup_labels = [r["day"] for r in signups_raw]
    signup_data   = [r["cnt"] for r in signups_raw]
    prob_labels   = [r["solved_date"] for r in probs_raw]
    prob_data     = [r["cnt"] for r in probs_raw]

    users_safe = []
    for u in users:
        d = dict(u)
        for f in ("password", "lc_password", "lc_session_cookie", "lc_csrf_token"):
            d.pop(f, None)
        users_safe.append(d)

    return jsonify({
        "users": users_safe, "counts": counts, "pending": pending,
        "total_subs": total_subs, "logs": rows_to_dicts(logs),
        "active_users": active_users,
        "signup_labels": signup_labels, "signup_data": signup_data,
        "prob_labels": prob_labels, "prob_data": prob_data,
        "custom_probs": rows_to_dicts(custom_probs)
    })


@app.route("/admin/verify/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_verify(uid):
    action = request.form.get("action", "approve")
    with get_db() as db:
        u = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
        if not u:
            return jsonify({"success": False, "message": "User not found."}), 404
        if action == "approve":
            db.execute("UPDATE users SET is_verified=1,status='active' WHERE id=%s", (uid,))
        else:
            db.execute("UPDATE users SET status='rejected' WHERE id=%s", (uid,))
        db.commit()
    log_admin("verify_user", u["username"], action)
    return jsonify({"success": True, "message": f"User {u['username']} {action}d."})


@app.route("/admin/delete/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_delete(uid):
    if uid == current_user.id:
        return jsonify({"success": False, "message": "Cannot delete yourself."}), 400
    with get_db() as db:
        u = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
        if u:
            # tables with user_id
            for tbl in [
                "submissions",
                "login_history",
                "sync_logs",
                "daily_challenges",
                "mentor_assignments"
            ]:
                db.execute(f"DELETE FROM {tbl} WHERE user_id=%s", (uid,))

            # follows table
            db.execute("""
            DELETE FROM follows
            WHERE follower_id=%s
            OR following_id=%s
            """, (uid, uid))

            # finally delete user
            db.execute("DELETE FROM users WHERE id=%s", (uid,))
            db.commit()
            log_admin("delete_user", u["username"])
    return jsonify({"success": True, "message": "User deleted."})


@app.route("/admin/promote/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_promote(uid):
    with get_db() as db:
        row = db.execute("SELECT username,is_admin FROM users WHERE id=%s", (uid,)).fetchone()
        if row:
            nv = 0 if row["is_admin"] else 1
            db.execute("UPDATE users SET is_admin=%s WHERE id=%s", (nv, uid))
            db.commit()
            log_admin("promote_user", row["username"],
                      "to admin" if nv else "to user")
    return jsonify({"success": True, "message": "Role updated."})


# ── Mentor Mode ───────────────────────────────────────────────────────────────
@app.route("/admin/mentor", methods=["GET", "POST"])
@login_required
@admin_required
def mentor():
    if request.method == "POST":
        uid   = int(request.form.get("user_id", 0))
        pname = sanitize(request.form.get("problem_name", ""), 200)
        purl  = sanitize(request.form.get("problem_url", ""), 300)
        diff  = request.form.get("difficulty", "Medium")
        plat  = request.form.get("platform", "LeetCode")
        topic = sanitize(request.form.get("topic", ""), 64)
        note  = sanitize(request.form.get("note", ""), 500)
        due   = request.form.get("due_date", "")
        with get_db() as db:
            db.execute("""
                INSERT INTO mentor_assignments
                (admin_id,user_id,problem_name,problem_url,difficulty,
                 platform,topic,note,assigned_date,due_date,completed)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
            """, (current_user.id, uid, pname, purl, diff, plat,
                  topic, note, datetime.now().strftime("%Y-%m-%d"), due))
            db.commit()
        return jsonify({"success": True, "message": "Problem assigned."})

    with get_db() as db:
        users = db.execute(
            "SELECT id,username FROM users WHERE status='active' AND is_admin=0"
        ).fetchall()
        assignments = db.execute("""
            SELECT ma.*,u.username
            FROM mentor_assignments ma
            JOIN users u ON ma.user_id=u.id
            ORDER BY ma.assigned_date DESC LIMIT 50
        """).fetchall()
        custom_probs = db.execute(
            "SELECT * FROM custom_problems ORDER BY created_at DESC"
        ).fetchall()

        # Build user_progress: total assigned vs completed per user
        user_progress = {}
        for u in users:
            uid = u["id"]
            total_assigned = db.execute(
                "SELECT COUNT(*) as c FROM mentor_assignments WHERE user_id=%s", (uid,)
            ).fetchone()["c"]
            total_completed = db.execute(
                "SELECT COUNT(*) as c FROM mentor_assignments WHERE user_id=%s AND completed=1", (uid,)
            ).fetchone()["c"]
            pct = int((total_completed / total_assigned * 100)) if total_assigned else 0
            user_progress[uid] = {
                "total": total_assigned,
                "completed": total_completed,
                "pct": pct
            }

    return jsonify({
        "users": rows_to_dicts(users),
        "assignments": rows_to_dicts(assignments),
        "custom_probs": rows_to_dicts(custom_probs),
        "user_progress": user_progress
    })


# ── Custom Problems ───────────────────────────────────────────────────────────
@app.route("/admin/custom_problem", methods=["POST"])
@login_required
@admin_required
def create_custom_problem():
    title = sanitize(request.form.get("title", ""), 200)
    desc  = sanitize(request.form.get("description", ""), 2000)
    diff  = request.form.get("difficulty", "Medium")
    topic = sanitize(request.form.get("topic", ""), 64)
    url   = sanitize(request.form.get("url", ""), 300)
    with get_db() as db:
        db.execute("""
            INSERT INTO custom_problems
            (created_by,title,description,difficulty,topic,url,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (current_user.id, title, desc, diff, topic, url,
              datetime.now().isoformat()))
        db.commit()
    log_admin("create_custom_problem", details=title)
    return jsonify({"success": True, "message": f"Custom problem '{title}' created."})


# ── Admin Sync All ────────────────────────────────────────────────────────────
@app.route("/admin/sync_user/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_sync_user(uid):
    def _do():
        with get_db() as db:
            u = db.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        if not u:
            return
        try:
            res = sync_user_data(dict(u), get_db)
            with get_db() as db:
                db.execute("""
                    INSERT INTO sync_logs (user_id,status,message,created_at)
                    VALUES (%s,%s,%s,%s)
                """, (uid,
                      "success" if res["success"] else "error",
                      res["message"], datetime.now().isoformat()))
                if res["success"]:
                    db.execute("UPDATE users SET last_sync=%s WHERE id=%s",
                               (datetime.now().strftime("%Y-%m-%d %H:%M"), uid))
                db.commit()
        except Exception as e:
            print(f"[SyncUser] {e}")
    run_background(_do)
    log_admin("sync_user", details=f"uid={uid}")
    return jsonify({"ok": True, "message": "Sync started"})


@app.route("/admin/reset_lc/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_reset_lc(uid):
    with get_db() as db:
        u = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()

        if u:
            db.execute("UPDATE users SET lc_imported=0 WHERE id=%s", (uid,))
            db.commit()
            log_admin("reset_lc_import", u["username"])
    return jsonify({"ok": True})


@app.route("/admin/sync_all", methods=["POST"])
@login_required
@admin_required
def admin_sync_all():
    def _do():
        with get_db() as db:
            users = db.execute(
                "SELECT * FROM users WHERE status='active' AND is_verified=1"
            ).fetchall()
        for u in users:
            try:
                res = sync_user_data(u, get_db)
                with get_db() as db:
                    db.execute("""
                        INSERT INTO sync_logs (user_id,status,message,created_at)
                        VALUES (%s,%s,%s,%s)
                    """, (u["id"],
                          "success" if res["success"] else "error",
                          res["message"], datetime.now().isoformat()))
                    if res["success"]:
                        db.execute("UPDATE users SET last_sync=%s WHERE id=%s",
                                   (datetime.now().strftime("%Y-%m-%d %H:%M"), u["id"]))
                    db.commit()
                time.sleep(3)
            except Exception as e:
                print(f"[AutoSync] {u['username']}: {e}")
    run_background(_do)
    log_admin("sync_all", details="Background sync triggered")
    return jsonify({"success": True, "message": "Background sync started for all users."})


# ── Restore Google Sheet from PostgreSQL (DB is always the source of truth) ──
@app.route("/admin/restore_sheet/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_restore_sheet(uid):
    with get_db() as db:
        u = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
    if not u:
        return jsonify({"ok": False, "message": "User not found"}), 404
    try:
        count = backfill_missing_rows_from_db(uid, u["username"], get_db)
        log_admin("restore_sheet", u["username"], f"{count} rows backfilled")
        return jsonify({"ok": True, "message": f"Backfilled {count} missing rows for {u['username']}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Backfill failed: {e}"}), 500


# One-time full re-sort — clears and rewrites the tab in correct date order.
# Use this once to fix ordering after rows got inserted out of place; use
# /admin/restore_sheet (backfill, above) for routine top-ups afterwards.
@app.route("/admin/rebuild_sheet/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_rebuild_sheet(uid):
    with get_db() as db:
        u = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
    if not u:
        return jsonify({"ok": False, "message": "User not found"}), 404
    try:
        count = rebuild_user_sheet_from_db(uid, u["username"], get_db)
        log_admin("rebuild_sheet", u["username"], f"{count} rows rebuilt")
        return jsonify({"ok": True, "message": f"Rebuilt {count} rows for {u['username']} in date order"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Rebuild failed: {e}"}), 500


@app.route("/admin/restore_all_sheets", methods=["POST"])
@login_required
@admin_required
def admin_restore_all_sheets():
    def _do():
        with get_db() as db:
            users = db.execute(
                "SELECT id, username FROM users WHERE status='active'"
            ).fetchall()
        for u in users:
            try:
                count = backfill_missing_rows_from_db(u["id"], u["username"], get_db)
                print(f"[RestoreAll] {u['username']}: {count} rows backfilled")
            except Exception as e:
                print(f"[RestoreAll] {u['username']}: {e}")
            time.sleep(2)  # stay under Google Sheets API rate limits
    run_background(_do)
    log_admin("restore_all_sheets", details="Background sheet restore triggered")
    return jsonify({"success": True, "message": "Restoring all users' sheets from the database in the background."})


# ── Backup / Restore (PostgreSQL via pg_dump / psql) ──────────────────────────
@app.route("/admin/backup", methods=["POST"])
@login_required
@admin_required
def admin_backup():
    ok, result = run_backup()
    if ok:
        log_admin("backup", details=result)
        return jsonify({"success": True, "message": f"Backup created: {result}"})
    return jsonify({"success": False, "message": f"Backup failed: {result}"}), 500


@app.route("/admin/restore", methods=["POST"])
@login_required
@admin_required
def admin_restore():
    fname = request.form.get("backup_file", "")
    ok, result = run_restore(fname)
    if ok:
        log_admin("restore", details=fname)
        return jsonify({"success": True, "message": result})
    return jsonify({"success": False, "message": f"Restore failed: {result}"}), 500


@app.route("/admin/backups_list")
@login_required
@admin_required
def admin_backups_list():
    return jsonify(list_backups())


# ── Student Management (backfill reg_no/roll_no/branch/full_name) ────────────
@app.route("/admin/students")
@login_required
@admin_required
def admin_students():
    with get_db() as db:
        users = db.execute("""
            SELECT id, username, email, full_name, reg_no, roll_no, branch, status
            FROM users
            WHERE is_admin=0
            ORDER BY (reg_no IS NULL OR reg_no = '') DESC, username ASC
        """).fetchall()
    missing_count = sum(1 for u in users if not u["reg_no"])
    return jsonify({"users": rows_to_dicts(users), "missing_count": missing_count})


@app.route("/admin/students/update/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_students_update(uid):
    full_name = sanitize(request.form.get("full_name", ""), 120)
    reg_no = sanitize(request.form.get("reg_no", ""), 40)
    roll_no = sanitize(request.form.get("roll_no", ""), 40)
    branch = sanitize(request.form.get("branch", ""), 40)

    with get_db() as db:
        if reg_no:
            clash = db.execute(
                "SELECT username FROM users WHERE reg_no=? AND reg_no != '' AND id != ?",
                (reg_no, uid)
            ).fetchone()
            if clash:
                return jsonify({"success": False, "message": f"Reg No '{reg_no}' is already used by {clash['username']}."}), 409

        db.execute("""
            UPDATE users SET full_name=?, reg_no=?, roll_no=?, branch=? WHERE id=?
        """, (full_name, reg_no, roll_no, branch, uid))
        db.commit()

    log_admin("update_student_fields", details=f"uid={uid}")
    return jsonify({"success": True, "message": "Student details updated."})