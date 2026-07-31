"""
routes/session_api.py — PART 5 (frontend/backend split), new file.

base.html used to render the nav bar (username, admin links, active-page
highlighting) server-side using Jinja's `current_user`. Now that the
frontend is a static site with no Jinja, it needs a JSON equivalent to
fetch on every page load — that's this endpoint. It reads the exact same
`current_user` flask-login object every other route already uses; no
auth/session logic is duplicated or changed.
"""

from flask import jsonify
from flask_login import current_user

from extensions import app


@app.route("/api/auth/me")
def api_auth_me():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})

    return jsonify({
        "authenticated": True,
        "id": current_user.id,
        "username": current_user.username,
        "is_admin": bool(current_user.is_admin),
        "is_verified": bool(current_user.is_verified),
        "status": current_user.status,
        "sheet_id": current_user.sheet_id or "",
    })
