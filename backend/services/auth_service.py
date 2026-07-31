"""
services/auth_service.py — the flask_login User model. Moved verbatim
from app.py.
"""

import json

from flask_login import UserMixin

from extensions import login_manager
from database.db import get_db


class User(UserMixin):
    def __init__(self, row):
        for k in row.keys():
            setattr(self, k, row[k])
        for attr in ["sheet_id", "cf_handle", "lc_handle", "lc_password", "lc_session_cookie", "lc_csrf_token",
                     "ac_handle", "bio", "email", "last_login", "last_sync", "lc_imported", "auto_sync_enabled", "sync_time"]:
            if not getattr(self, attr, None):
                setattr(self, attr, "")
        # cookie_expiry: default 0 (not expired)
        if not hasattr(self, "cookie_expiry") or self.cookie_expiry is None:
            self.cookie_expiry = 0
        if not getattr(self, "enabled_platforms", None):
            self.enabled_platforms = '["Codeforces","LeetCode","AtCoder"]'
        if not getattr(self, "lc_imported", None):
            self.lc_imported = 0
        if not getattr(self, "auto_sync_enabled", None):
            self.auto_sync_enabled = 1
        if not getattr(self, "sync_time", None):
            self.sync_time = "09:00"

    @property
    def enabled_list(self):
        try:
            return json.loads(self.enabled_platforms)
        except Exception:
            return ["Codeforces", "LeetCode", "AtCoder"]


@login_manager.user_loader
def load_user(uid):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return User(row) if row else None
