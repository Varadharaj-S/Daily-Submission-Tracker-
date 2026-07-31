"""
routes/leaderboard.py — public profile pages, follow/unfollow, the
friends list, the leaderboard, and per-user login history. Moved
verbatim from app.py.
"""

from datetime import datetime

from flask import jsonify, abort
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from utils.helpers import get_counts, rows_to_dicts
from services.auth_service import User
from services.sync_engine import get_dashboard_data


# ── Public Profile ────────────────────────────────────────────────────────────
@app.route("/user/<username>")
def public_profile(username):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND status='active'", (username,)
        ).fetchone()
        if not row:
            abort(404)
        user = User(row)
        self_view = current_user.is_authenticated and current_user.id == user.id
        admin_view = current_user.is_authenticated and current_user.is_admin
        if not user.is_public and not self_view and not admin_view:
            abort(403)
        subs = db.execute("""
SELECT * FROM submissions 
WHERE user_id=? 
ORDER BY 
    substr(solved_date,7,4) DESC,
    substr(solved_date,4,2) DESC,
    substr(solved_date,1,2) DESC
""", (current_user.id,)).fetchall()
        fol_c = db.execute("SELECT COUNT(*) as c FROM follows WHERE following_id=?",
                           (user.id,)).fetchone()["c"]
        fwg_c = db.execute("SELECT COUNT(*) as c FROM follows WHERE follower_id=?",
                           (user.id,)).fetchone()["c"]
        is_following = False
        if current_user.is_authenticated and not self_view:
            is_following = bool(db.execute(
                "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
                (current_user.id, user.id)
            ).fetchone())
    data = get_dashboard_data(subs)
    total, solved = get_counts(current_user.id)
    data["total"] = total
    data["solved"] = solved
    profile_user_dict = {k: v for k, v in vars(user).items() if k not in
                          ("password", "lc_password", "lc_session_cookie", "lc_csrf_token")}
    return jsonify({
        "profile_user": profile_user_dict, "data": data, "subs": rows_to_dicts(list(subs)[:20]),
        "follower_count": fol_c, "following_count": fwg_c,
        "is_following": is_following, "viewer_is_self": self_view
    })


# ── Follow ─────────────────────────────────────────────────────────────────────
@app.route("/follow/<int:uid>", methods=["POST"])
@login_required
@verified_required
def follow(uid):
    if uid == current_user.id:
        return jsonify({"success": False, "message": "Cannot follow yourself."})
    with get_db() as db:
        ex = db.execute("SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
                        (current_user.id, uid)).fetchone()
        if ex:
            db.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?",
                       (current_user.id, uid))
            action = "unfollowed"
        else:
            db.execute("INSERT INTO follows (follower_id,following_id,created_at) VALUES (?,?,?)",
                       (current_user.id, uid, datetime.now().isoformat()))
            action = "followed"
        cnt = db.execute("SELECT COUNT(*) as c FROM follows WHERE following_id=?",
                         (uid,)).fetchone()["c"]
        db.commit()
    return jsonify({"success": True, "action": action, "count": cnt})


# ── Friends / Leaderboard ──────────────────────────────────────────────────────
@app.route("/friends")
@login_required
@verified_required
def friends():
    with get_db() as db:
        following = db.execute("""
            SELECT u.id,u.username,u.cf_handle,u.lc_handle,u.ac_handle,
                   u.last_sync,u.is_public,
                   (SELECT COUNT(*) FROM submissions WHERE user_id=u.id) as prob_count
            FROM follows f JOIN users u ON f.following_id=u.id
            WHERE f.follower_id=? AND u.status='active'
            ORDER BY prob_count DESC
        """, (current_user.id,)).fetchall()
        followers = db.execute("""
            SELECT u.id,u.username,u.is_public,
                   (SELECT COUNT(*) FROM submissions WHERE user_id=u.id) as prob_count
            FROM follows f JOIN users u ON f.follower_id=u.id
            WHERE f.following_id=? AND u.status='active'
        """, (current_user.id,)).fetchall()
    return jsonify({"following": rows_to_dicts(following), "followers": rows_to_dicts(followers)})


@app.route("/leaderboard")
@login_required
@verified_required
def leaderboard():
    with get_db() as db:
        users = db.execute("""
            SELECT u.id,u.username,u.is_public,
                   COUNT(s.id) as total,
                   SUM(CASE WHEN s.platform='Codeforces' THEN 1 ELSE 0 END) as cf,
                   SUM(CASE WHEN s.platform='LeetCode'   THEN 1 ELSE 0 END) as lc,
                   SUM(CASE WHEN s.platform='AtCoder'    THEN 1 ELSE 0 END) as ac,
                   u.last_sync
            FROM users u
            LEFT JOIN submissions s ON u.id=s.user_id
            WHERE u.status='active' AND u.is_verified=1 AND u.is_admin=0
            GROUP BY u.id ORDER BY total DESC LIMIT 50
        """).fetchall()
    return jsonify({"users": rows_to_dicts(users)})


@app.route("/login_history")
@login_required
@verified_required
def login_history():
    with get_db() as db:
        history = db.execute(
            "SELECT * FROM login_history WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (current_user.id,)
        ).fetchall()
    return jsonify({"history": rows_to_dicts(history)})
