"""
routes/extension.py — Chrome-extension pairing.

The extension can't hold the Flask browser session (it's a separate
origin with its own storage), so /save_cookie can't use @login_required
the way every other authenticated route does. Instead:

  1. The logged-in user calls POST /extension/generate-token (normal
     session auth, like every other route here) and gets back a random
     token, stored on their own `users.extension_token` row.
  2. They paste that token into the extension popup once. The extension
     keeps it in chrome.storage.local and sends it as
     `Authorization: Bearer <token>` on every /save_cookie call.
  3. /save_cookie looks the user up FROM the token — the extension never
     sends (and the server never trusts) a user id.

One active token per user: generating a new one overwrites/invalidates
the old one, and /extension/revoke-token clears it outright.
"""

import secrets

from flask import request, jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required


def _find_user_by_extension_token(token):
    """Looks up the user row that owns this extension token. Returns None
    if the token is missing/blank or doesn't match anyone."""
    if not token:
        return None
    with get_db() as db:
        return db.execute(
            "SELECT id FROM users WHERE extension_token=?", (token,)
        ).fetchone()


# ── Generate / regenerate extension token ────────────────────────────────────
@app.route("/extension/generate-token", methods=["POST"])
@login_required
def generate_extension_token():
    """Issues a fresh pairing token for the CURRENTLY LOGGED-IN user only.
    Overwrites any previous token for this user, so the old one stops
    working the moment a new one is generated (one active token/user)."""
    token = secrets.token_urlsafe(32)

    with get_db() as db:
        db.execute(
            "UPDATE users SET extension_token=? WHERE id=?",
            (token, current_user.id)
        )
        db.commit()

    return jsonify({"success": True, "token": token})


# ── Revoke extension token ────────────────────────────────────────────────────
@app.route("/extension/revoke-token", methods=["POST"])
@login_required
def revoke_extension_token():
    """Clears the current user's extension token. Any extension still
    holding the old value will get 401s on /save_cookie until re-paired."""
    with get_db() as db:
        db.execute(
            "UPDATE users SET extension_token=NULL WHERE id=?",
            (current_user.id,)
        )
        db.commit()

    return jsonify({"success": True, "message": "Extension token revoked."})


# ── Pairing status (for the settings page) ────────────────────────────────────
@app.route("/extension/status", methods=["GET"])
@login_required
def extension_status():
    """Whether the current user has ever paired an extension. Does NOT
    return the token itself — a page reload should never leak it back."""
    with get_db() as db:
        row = db.execute(
            "SELECT extension_token FROM users WHERE id=?", (current_user.id,)
        ).fetchone()
    return jsonify({"paired": bool(row and row.get("extension_token"))})


# ── Chrome Extension: Save LeetCode Cookie (Bearer token auth) ───────────────
@app.route("/save_cookie", methods=["POST"])
def save_cookie_extension():
    """
    Called by the Chrome extension popup. Unlike every other route in this
    file, this one is intentionally NOT @login_required — the extension has
    no Flask session. It authenticates via `Authorization: Bearer <token>`
    instead, where <token> is the value from /extension/generate-token.

    The server determines which user to save cookies for FROM the token
    (token -> user lookup below). The request body is never trusted to name
    a user_id.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({
            "success": False,
            "message": "Missing extension token. Please pair the extension with your DSA Tracker account first."
        }), 401

    token = auth_header[len("Bearer "):].strip()
    user = _find_user_by_extension_token(token)
    if not user:
        return jsonify({
            "success": False,
            "message": "Extension token is invalid or has been revoked. Please pair the extension again."
        }), 401

    data = request.get_json(force=True, silent=True) or {}
    lc_session = (data.get("leetcode_session") or "").strip()[:2000]
    csrf_token = (data.get("csrf_token") or "").strip()[:200]

    if not lc_session or not csrf_token:
        return jsonify({"success": False, "message": "Missing session or CSRF token"}), 400

    with get_db() as db:
        db.execute("""
            UPDATE users SET
                lc_session_cookie=?,
                lc_csrf_token=?,
                cookie_expiry=0
            WHERE id=?
        """, (lc_session, csrf_token, user["id"]))
        db.commit()

    return jsonify({
        "success": True,
        "message": "Cookie saved"
    })