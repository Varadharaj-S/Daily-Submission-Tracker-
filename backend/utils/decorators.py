"""
utils/decorators.py — auth/authorization decorators used across routes.
Moved verbatim from app.py. `login_required` is just re-exported from
flask_login so routes only need one import line.

PART 5 (frontend/backend split): admin_required/verified_required used to
flash a message and redirect to a Jinja-rendered page. There is no such
page anymore — the frontend is a separate static site — so both now
return a plain JSON response with an HTTP status the frontend's shared
fetch helper (assets/js/app.js) already knows how to handle, plus a
"redirect" hint the frontend can act on if it wants to.
"""

from functools import wraps
from datetime import datetime

from flask import jsonify, request
from flask_login import login_required, current_user  # noqa: F401 (re-exported)

from database.db import get_db

__all__ = ["login_required", "admin_required", "verified_required", "log_admin"]


def admin_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({
                "success": False,
                "message": "Admin access required.",
                "redirect": "/dashboard.html"
            }), 403
        return f(*a, **kw)
    return wrapped


def verified_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if not current_user.is_verified or current_user.status != "active":
            return jsonify({
                "success": False,
                "message": "Your account is pending verification/approval.",
                "redirect": "/pending.html"
            }), 403
        return f(*a, **kw)
    return wrapped


def log_admin(action, target="", details=""):
    with get_db() as db:
        db.execute("""
            INSERT INTO admin_logs
            (admin_id,action,target_user,details,ip,created_at)
            VALUES (?,?,?,?,?,?)
        """, (current_user.id, action, target, details,
              request.remote_addr, datetime.now().isoformat()))
        db.commit()
