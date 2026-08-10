"""
routes/settings.py — user account settings, the Chrome-extension cookie
endpoints, LeetCode browser-login connect, sync-time/auto-sync toggles,
and the feedback form. Moved verbatim from app.py.
"""

import os
import json
import time
import smtplib
from email.mime.text import MIMEText

from flask import request, jsonify
from flask_login import current_user
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import app
from database.db import get_db
from utils.decorators import login_required, verified_required
from utils.security import sanitize
from services.sync_engine import webdriver, Service, ChromeDriverManager


# ── User Settings ─────────────────────────────────────────────────────────────
@app.route("/save_settings", methods=["POST"])
@login_required
def save_settings():
    data = request.get_json() or {}

    with get_db() as db:
        db.execute("""
        UPDATE users SET
            lc_handle=?,
            cf_handle=?,
            ac_handle=?,
            lc_session_cookie=?,
            lc_csrf_token=?
        WHERE id=?
        """, (
            data.get("leetcode", ""),
            data.get("codeforces", ""),
            data.get("atcoder", ""),
            data.get("cookie", ""),
            data.get("csrf", ""),
            current_user.id
        ))

        db.commit()

    return jsonify({"status": "saved"})


# ── Chrome Extension: Save LeetCode Cookie ────────────────────────────────────
# Moved to routes/ext_pairing.py: the extension has no Flask session to send,
# so this endpoint can't use @login_required like the rest of this file.
# It now authenticates via `Authorization: Bearer <extension_token>` instead
# — see routes/ext_pairing.py (save_cookie_extension) and
# /extension/generate-token for how a user pairs the extension in the first
# place. Kept registered under the same /save_cookie URL and same request/
# response shape, so nothing else in this file changes.


# ── Chrome Extension: Cookie Status ───────────────────────────────────────────
@app.route("/cookie_status")
@login_required
def cookie_status():
    """Returns whether the current user has a valid LeetCode cookie stored."""
    with get_db() as db:
        user = db.execute(
            "SELECT lc_session_cookie, cookie_expiry FROM users WHERE id=?",
            (current_user.id,)
        ).fetchone()
    has_cookie = bool(user and user["lc_session_cookie"])
    expired = bool(user and user.get("cookie_expiry", 0))
    return jsonify({
        "connected": has_cookie and not expired,
        "expired": expired
    })


# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/settings", methods=["GET", "POST"])
@login_required
@verified_required
def settings():
    if request.method == "GET":
        with get_db() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
        user_dict = dict(row) if row else {}
        for f in ("password", "lc_password", "lc_session_cookie", "lc_csrf_token"):
            user_dict.pop(f, None)
        return jsonify({"user": user_dict})

    action = request.form.get("action", "profile") if not request.is_json else (request.json or {}).get("action", "profile")
    body = request.form if not request.is_json else (request.json or {})
    with get_db() as db:
        if action == "profile":
            cf = sanitize(body.get("cf_handle", ""), 64)
            lc = sanitize(body.get("lc_handle", ""), 64)
            lc_pw = (body.get("lc_password", "") or "").strip()[:128]
            lc_cookie = (body.get("lc_session_cookie", "") or "").strip()[:500]
            lc_csrf = (body.get("lc_csrf_token", "") or "").strip()[:100]
            ac = sanitize(body.get("ac_handle", ""), 64)
            sid = sanitize(body.get("sheet_id", ""), 128)
            bio = sanitize(body.get("bio", ""), 300)
            pub = 1 if body.get("is_public") else 0
            plat = request.form.getlist("platforms") if not request.is_json else (body.get("platforms") or [])
            if not plat:
                plat = ["Codeforces", "LeetCode", "AtCoder"]
            valid_plat = [p for p in plat
                          if p in ["Codeforces", "LeetCode", "AtCoder"]]
            db.execute("""
                UPDATE users SET cf_handle=?,lc_handle=?,lc_password=?,
                lc_session_cookie=?,lc_csrf_token=?,ac_handle=?,sheet_id=?,bio=?,is_public=?,
                enabled_platforms=? WHERE id=?
            """, (cf, lc,
                  generate_password_hash(lc_pw) if lc_pw else current_user.lc_password,
                  lc_cookie, lc_csrf,
                  ac, sid, bio, pub,
                  json.dumps(valid_plat), current_user.id))
            db.commit()
            print("COOKIE:", lc_cookie[:20])
            print("CSRF:", lc_csrf)
            return jsonify({"success": True, "message": "Profile settings saved!"})

        elif action == "password":
            old = body.get("old_password", "")
            new = body.get("new_password", "")
            conf = body.get("confirm_password", "")
            row = db.execute("SELECT password FROM users WHERE id=?",
                              (current_user.id,)).fetchone()
            if not check_password_hash(row["password"], old):
                return jsonify({"success": False, "message": "Current password incorrect."}), 400
            elif new != conf:
                return jsonify({"success": False, "message": "New passwords don't match."}), 400
            elif len(new) < 6:
                return jsonify({"success": False, "message": "Min 6 characters."}), 400
            else:
                db.execute("UPDATE users SET password=? WHERE id=?",
                           (generate_password_hash(new), current_user.id))
                db.commit()
                return jsonify({"success": True, "message": "Password changed!"})
    return jsonify({"success": False, "message": "Unknown action."}), 400


# ── LeetCode Connect (browser automation) ─────────────────────────────────────
@app.route("/leetcode/connect", methods=["POST"])
@login_required
def leetcode_connect():
    if webdriver is None or Service is None or ChromeDriverManager is None:
        return jsonify({"success": False, "message": "Browser automation dependencies are not installed."}), 503

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install())
    )

    driver.get("https://leetcode.com/accounts/login/")

    for _ in range(120):
        cookies = driver.get_cookies()
        names = [c["name"] for c in cookies]

        if "LEETCODE_SESSION" in names:
            break

        time.sleep(1)

    if "LEETCODE_SESSION" not in names:
        driver.quit()

        return jsonify({
            "success": False,
            "message": "Login timeout. Please try again."
        })
    lc_session = None
    csrf_token = None

    for cookie in cookies:
        if cookie["name"] == "LEETCODE_SESSION":
            lc_session = cookie["value"]
        elif cookie["name"] == "csrftoken":
            csrf_token = cookie["value"]

    if lc_session and csrf_token:
        with get_db() as db:
            db.execute(
                """
                UPDATE users
                SET lc_session_cookie=?,
                    lc_csrf_token=?
                WHERE id=?
                """,
                (
                    lc_session,
                    csrf_token,
                    current_user.id
                )
            )
            db.commit()

        driver.quit()

        return jsonify({
            "success": True,
            "message": "LeetCode Connected"
        })

    driver.quit()

    return jsonify({
        "success": False,
        "message": "Login Failed"
    })


# ── Sync-time / auto-sync toggle ──────────────────────────────────────────────
@app.route("/set_sync_time", methods=["POST"])
@login_required
def set_sync_time():
    data = request.json or {}
    sync_time = data.get("time", "09:00")

    with get_db() as db:
        db.execute(
            "UPDATE users SET sync_time=? WHERE id=?",
            (sync_time, current_user.id)
        )
        db.commit()

    return jsonify({"success": True, "sync_time": sync_time})


@app.route("/toggle_auto_sync", methods=["POST"])
@login_required
def toggle_auto_sync():
    data = request.json or {}
    enabled = 1 if data.get("enabled") else 0
    with get_db() as db:
        db.execute(
            "UPDATE users SET auto_sync_enabled=? WHERE id=?",
            (enabled, current_user.id)
        )
        db.commit()
    return jsonify({"success": True, "enabled": bool(enabled)})


# ── Feedback ───────────────────────────────────────────────────────────────────
@app.route("/feedback", methods=["POST"])
@login_required
def feedback():
    try:
        data = request.get_json() or {}

        feedback_text = data.get("feedback", "").strip()

        if not feedback_text:
            return jsonify({
                "success": False,
                "message": "Feedback is empty"
            })

        # Uses Resend API (same as email_service.py on Render).
        # Env vars needed: RESEND_API_KEY, FROM_EMAIL, ADMIN_EMAIL
        resend_api_key = os.environ.get("RESEND_API_KEY", "")
        from_email     = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")
        admin_email    = os.environ.get("ADMIN_EMAIL") or from_email

        if not resend_api_key:
            print("[Feedback] missing RESEND_API_KEY")
            return jsonify({"success": False, "message": "Email not configured on server."})

        import urllib.request
        import urllib.error
        import json as _json

        # DEBUG: log key/from/admin so we can rule out env var corruption
        # (trailing \n, stray quotes, wrong slot) without printing the full key.
        print(f"[Feedback] key_len={len(resend_api_key)} "
              f"key_repr={resend_api_key[:6]!r}...{resend_api_key[-4:]!r} "
              f"from={from_email!r} admin={admin_email!r}")

        payload = _json.dumps({
            "from": from_email,
            "to": [admin_email],
            "subject": "DSA Tracker Feedback",
            "text": f"User: {current_user.username}\nEmail: {getattr(current_user, 'email', '')}\n\nFeedback:\n\n{feedback_text}"
        }).encode()

        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {resend_api_key.strip()}",
                "Content-Type": "application/json",
                # Resend's Cloudflare front blocks requests with no/generic
                # User-Agent (error code 1010). urllib doesn't send a real
                # one by default, so we set it explicitly.
                "User-Agent": "DSATracker/1.0 (+feedback-form)",
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req) as resp:
                print("[Feedback] Resend response:", resp.status)
        except urllib.error.HTTPError as he:
            body = he.read().decode(errors="ignore")
            print(f"[Feedback] Resend HTTPError {he.code}: {body}")
            return jsonify({
                "success": False,
                "message": f"Resend {he.code}: {body}"
            })
        except urllib.error.URLError as ue:
            print(f"[Feedback] Resend URLError: {ue.reason}")
            return jsonify({
                "success": False,
                "message": f"Could not reach Resend: {ue.reason}"
            })

        return jsonify({
            "success": True,
            "message": "Feedback sent successfully"
        })

    except Exception as e:
        print("FEEDBACK ERROR =", e)

        return jsonify({
            "success": False,
            "message": str(e)
        })