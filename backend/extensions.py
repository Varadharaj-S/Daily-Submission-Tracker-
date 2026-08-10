

import os
import time
import threading

from flask import Flask, request, session, jsonify
from flask_login import LoginManager, current_user, logout_user
from flask_cors import CORS

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(Config.BACKUP_DIR, exist_ok=True)  # Config.BACKUP_DIR is /tmp/backups on Vercel, "backups" elsewhere

login_manager = LoginManager(app)
login_manager.session_protection = "basic"


@login_manager.unauthorized_handler
def _unauthorized():
    """PART 5 (frontend/backend split): there is no more login.html for
    Flask to redirect to — the frontend is a separate static site on a
    different domain. API callers get a plain 401 JSON response instead;
    the frontend's shared JS (see assets/js/app.js) checks for 401s and
    sends the browser to login.html itself."""
    return jsonify({"success": False, "message": "Please log in to continue."}), 401


# CORS: exact origin(s) from Config.FRONTEND_ORIGINS (env-driven, see
# config.py) with credentials allowed, since the session cookie needs to
# travel cross-site from the Cloudflare Pages frontend to this API.
CORS(app, supports_credentials=True, origins=Config.FRONTEND_ORIGINS)


@app.before_request
def _enforce_idle_timeout():
    """If the user has been inactive longer than IDLE_TIMEOUT_MINUTES, force
    logout. Runs before every request so a stale 'remembered' session can't
    silently keep serving pages after the person has walked away."""
    if current_user.is_authenticated:
        now = time.time()
        last_active = session.get("_last_active")
        timeout_s = app.config["IDLE_TIMEOUT_MINUTES"] * 60
        if last_active is not None and (now - last_active) > timeout_s:
            logout_user()
            session.clear()
        else:
            session["_last_active"] = now
            session.permanent = True


@app.after_request
def _no_cache_after_auth_pages(response):
    if not request.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response