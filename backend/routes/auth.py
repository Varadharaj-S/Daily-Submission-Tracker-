"""
routes/auth.py — index redirect, login, signup, email verification,
logout, and the separate admin login. Moved verbatim from app.py.

PART 5 (frontend/backend split): every route below used to call
render_template(...) or flash()+redirect(url_for(...)) to hand a
server-rendered page back to the browser. There is no more Jinja
rendering on this backend — the frontend (login.html, signup.html, etc.)
now lives on Cloudflare Pages as static files and talks to these same
routes via fetch(). So GET routes that only rendered a page are removed
(the static HTML file *is* the page now), and POST routes now return
JSON ({"success": bool, "message": str, "redirect": "<frontend path>"})
instead of a redirect. All DB logic, validation, and session/login_user
calls are byte-for-byte the same as before.
"""

import re
import time
import secrets
import threading
from datetime import datetime, timedelta

from flask import request, redirect, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import app
from config import Config
from database.db import get_db, IntegrityError
from utils.security import sanitize, rate_limit
from services.auth_service import User
from services.email_service import send_verification_email
from services.year_sheet_service import list_configured_years, is_year_configured


def _frontend_url(path):
    """Build an absolute URL on the configured frontend for the rare cases
    (an email link clicked directly in the browser) where Flask has to
    redirect the browser itself instead of returning JSON to a fetch()
    call. Uses the first configured FRONTEND_ORIGINS entry — set that env
    var to your real frontend domain."""
    origin = Config.FRONTEND_ORIGINS[0] if Config.FRONTEND_ORIGINS else ""
    return f"{origin.rstrip('/')}/{path.lstrip('/')}"


# ── Index ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return jsonify({"authenticated": True, "redirect": "/admin.html"})
        if not current_user.is_verified or current_user.status != "active":
            return jsonify({"authenticated": True, "redirect": "/pending.html"})
        return jsonify({"authenticated": True, "redirect": "/dashboard.html"})
    return jsonify({"authenticated": False, "redirect": "/login.html"})


# ── User Auth ─────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
@rate_limit(max_calls=10, window=60)
def login():
    if request.method == "GET":
        if current_user.is_authenticated:
            return jsonify({"authenticated": True, "redirect": "/dashboard.html"})
        return jsonify({"authenticated": False})

    username = sanitize(request.form.get("username", "") or (request.json or {}).get("username", "") if request.is_json else request.form.get("username", ""))
    password = request.form.get("password", "") if not request.is_json else (request.json or {}).get("password", "")
    ip, ua = request.remote_addr, request.user_agent.string[:200]

    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND is_admin=0", (username,)
        ).fetchone()
        ok = bool(row and check_password_hash(row["password"], password))
        db.execute("""
            INSERT INTO login_history
            (user_id,ip,user_agent,success,created_at)
            VALUES (?,?,?,?,?)
        """, (row["id"] if row else None, ip, ua,
              1 if ok else 0, datetime.now().isoformat()))
        if ok:
            db.execute("UPDATE users SET last_login=? WHERE id=?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M"), row["id"]))
        db.commit()

    if ok:
        user = User(row)
        if row["status"] != "active":
            return jsonify({
                "success": False,
                "message": "Your account is pending admin verification.",
                "redirect": "/pending.html"
            }), 403
        session.clear()

        login_user(
            user,
            remember=False,
            fresh=True
        )

        session.permanent = True
        session["_last_active"] = time.time()
        return jsonify({
            "success": True,
            "message": f"Welcome back, {username}!",
            "redirect": "/dashboard.html"
        })

    return jsonify({"success": False, "message": "Invalid username or password."}), 401


@app.route("/signup", methods=["GET", "POST"])
@rate_limit(max_calls=5, window=300)
def signup():
    if request.method == "GET":
        # PHASE 2: the signup form needs the list of years a mentor has
        # actually configured a sheet for, so it can offer a dropdown
        # instead of a free-text field a student could typo.
        return jsonify({"ok": True, "years": list_configured_years()})

    body = request.form if not request.is_json else (request.json or {})
    username = sanitize(body.get("username", ""), 32)
    email = sanitize(body.get("email", ""), 120)
    password = body.get("password", "")
    confirm = body.get("confirm", "")
    full_name = sanitize(body.get("full_name", ""), 120)
    reg_no = sanitize(body.get("reg_no", ""), 40)
    roll_no = sanitize(body.get("roll_no", ""), 40)
    branch = sanitize(body.get("branch", ""), 40)
    cohort_year = sanitize(body.get("cohort_year", "") or body.get("year", ""), 16)

    if not username or len(username) < 3:
        return jsonify({"success": False, "message": "Username must be at least 3 characters."}), 400
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return jsonify({"success": False, "message": "Username: letters, numbers, _ only."}), 400
    if not full_name:
        return jsonify({"success": False, "message": "Full name is required."}), 400
    if not reg_no:
        return jsonify({"success": False, "message": "Register number is required."}), 400
    if not branch:
        return jsonify({"success": False, "message": "Branch is required."}), 400
    if not cohort_year:
        return jsonify({"success": False, "message": "Year/Cohort is required."}), 400
    # Backend validation, never trust the client value alone: the year
    # must be one a mentor has actually configured a sheet for.
    if not is_year_configured(cohort_year):
        return jsonify({"success": False, "message": f"'{cohort_year}' isn't an available year yet. Contact your mentor."}), 400
    if password != confirm:
        return jsonify({"success": False, "message": "Passwords do not match."}), 400
    if len(password) < 6:
        return jsonify({"success": False, "message": "Password min. 6 characters."}), 400

    try:
        with get_db() as db:
            existing_reg = db.execute(
                "SELECT 1 FROM users WHERE reg_no=? AND reg_no != ''", (reg_no,)
            ).fetchone()
            if existing_reg:
                return jsonify({"success": False, "message": "That register number is already registered."}), 409

            db.execute("""
                INSERT INTO users
                (username,email,password,is_verified,status,
                 enabled_platforms,created_at,full_name,reg_no,roll_no,branch,cohort_year)
                VALUES (?,?,?,0,'pending',?,?,?,?,?,?,?)
            """, (username, email, generate_password_hash(password),
                  '["Codeforces","LeetCode","AtCoder"]',
                  datetime.now().isoformat(),
                  full_name, reg_no, roll_no, branch, cohort_year))
            db.commit()
            user_row = db.execute(
                "SELECT id FROM users WHERE username=?", (username,)
            ).fetchone()
            uid = user_row["id"]
            token = secrets.token_urlsafe(32)
            db.execute("""
                INSERT INTO email_tokens (user_id, token, created_at)
                VALUES (?,?,?)
            """, (uid, token, datetime.now().isoformat()))
            db.commit()

        threading.Thread(
            target=send_verification_email,
            args=(email, username, token),
            daemon=True
        ).start()

        return jsonify({
            "success": True,
            "message": "Account created! Please check your email to verify your address, then wait for admin approval.",
            "redirect": "/pending.html"
        })
    except IntegrityError:
        return jsonify({"success": False, "message": "Username already taken."}), 409


def _create_sheet_bg(username, email):
    """Background: create Google Sheet and save ID to user record.
    NOTE: preserved from the original app.py, where this was also defined
    but never actually called from anywhere (signup() only threads
    send_verification_email, not this). Kept as-is rather than silently
    dropped or wired in, since either change would be a behavior change,
    not a refactor."""
    from services.sync_engine import create_user_sheet
    sheet_id, msg = create_user_sheet(email, username)
    if sheet_id:
        with get_db() as db:
            db.execute("UPDATE users SET sheet_id=? WHERE username=?",
                       (sheet_id, username))
            db.commit()
        print(f"[Sheets] Created sheet for {username}: {sheet_id}")
    else:
        print(f"[Sheets] Failed for {username}: {msg}")


@app.route("/verify_email/<token>")
def verify_email(token):
    # This route is reached by the browser clicking a link in an email, not
    # by a fetch() call from the frontend — so unlike the rest of this
    # file, it still returns an actual HTTP redirect (to the configured
    # frontend), just now carrying the message as a query param instead of
    # a Flask flash() (there's no Jinja page left to read the flash here).
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM email_tokens WHERE token=? AND used=0", (token,)
        ).fetchone()
        if not row:
            return redirect(_frontend_url("/login.html?error=" + "Invalid or expired verification link."))

        # Check token not older than 24h
        created = datetime.fromisoformat(row["created_at"])
        if datetime.now() - created > timedelta(hours=24):
            return redirect(_frontend_url("/login.html?error=" + "Verification link has expired. Please contact admin."))

        uid = row["user_id"]
        db.execute("UPDATE email_tokens SET used=1 WHERE token=?", (token,))
        db.execute("UPDATE users SET is_verified=1 WHERE id=? AND status='pending'", (uid,))
        # Note: status stays 'pending' until admin approves — is_verified=1 just means email confirmed
        db.commit()

        user_row = db.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
        username = user_row["username"] if user_row else "User"

    return redirect(_frontend_url(f"/login.html?success=Email verified! Hi {username} — your account is now pending admin approval."))


@app.route("/pending")
def pending():
    return jsonify({"ok": True})


@app.route("/logout")
@login_required
def logout():
    logout_user()

    session.clear()

    resp = jsonify({"success": True, "message": "Logged out successfully.", "redirect": "/login.html"})

    resp.delete_cookie("remember_token")
    resp.delete_cookie(app.config["SESSION_COOKIE_NAME"])

    return resp


# ── Separate Admin Login ───────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET", "POST"])
@rate_limit(max_calls=5, window=60)
def admin_login():
    if request.method == "GET":
        if current_user.is_authenticated and current_user.is_admin:
            return jsonify({"authenticated": True, "redirect": "/admin.html"})
        return jsonify({"authenticated": False})

    body = request.form if not request.is_json else (request.json or {})
    username = sanitize(body.get("username", ""))
    password = body.get("password", "")
    ip, ua = request.remote_addr, request.user_agent.string[:200]
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND is_admin=1", (username,)
        ).fetchone()
        ok = bool(row and check_password_hash(row["password"], password))
        db.execute("""
            INSERT INTO login_history (user_id,ip,user_agent,success,created_at)
            VALUES (?,?,?,?,?)
        """, (row["id"] if row else None, ip, ua,
              1 if ok else 0, datetime.now().isoformat()))
        if ok:
            db.execute("UPDATE users SET last_login=? WHERE id=?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M"), row["id"]))
        db.commit()
    if ok:
        session.clear()

        login_user(
            User(row),
            remember=False,
            fresh=True
        )

        session.permanent = True
        session["_last_active"] = time.time()
        return jsonify({
            "success": True,
            "message": f"Admin login: {username}",
            "redirect": "/admin.html"
        })
    return jsonify({"success": False, "message": "Invalid admin credentials."}), 401
