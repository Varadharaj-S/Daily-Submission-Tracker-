"""
config.py — application configuration, read from environment variables.

Same values the old app.py set inline on app.config; nothing here changes
behavior, it just gives them one home instead of being scattered at the top
of the monolith.
"""

import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv()


class Config:
    # SECURITY: load from env, generate random fallback (never hardcoded).
    # NOTE: if SECRET_KEY isn't set, a new random key is generated every
    # process start, which invalidates existing sessions on restart — this
    # matches the original app.py behavior exactly. Set SECRET_KEY in your
    # environment for production so restarts don't log everyone out.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # ── Frontend/backend split (PART 5) ──────────────────────────────────
    # The frontend (Cloudflare Pages) and backend (Vercel) are now on two
    # different domains, so the login-session cookie is a cross-site
    # cookie. That requires SameSite=None + Secure=True (browsers refuse
    # to send SameSite=Lax/Strict cookies cross-site). Everything here is
    # env-driven so the actual domains can change later without touching
    # any code — see .env.example.
    #
    # FRONTEND_ORIGINS: comma-separated list of exact origins allowed to
    # call this API with credentials (e.g. your Cloudflare Pages URL,
    # plus http://localhost:5500 for local frontend dev). CORS requires
    # an exact origin match when supports_credentials=True — "*" does not
    # work with cookies.
    FRONTEND_ORIGINS = [
        o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "http://localhost:5500").split(",")
        if o.strip()
    ]

    # PART 6 update: frontend/_redirects now proxies /api/* through the
    # Cloudflare frontend domain to this backend, so in production the
    # cookie is first-party (same-site) again — no more SameSite=None
    # needed, which is what let Safari/Brave/strict-Firefox silently drop
    # the login cookie for some visitors. Default is now "Lax". If you're
    # running the frontend locally on localhost WITHOUT the Cloudflare
    # proxy in front of it (calling this backend directly, cross-site),
    # set SESSION_COOKIE_SAMESITE=None in your local .env for that case.
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True

    # Idle/inactivity timeout (fixes: open link should always show login,
    # back-button should not reveal a cached logged-in page after logout).
    IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "20"))

    # BACKUP_DIR: on Vercel the deployment filesystem is read-only except for
    # /tmp, so a relative "backups" folder can't be created there and would
    # crash at import time. Vercel sets the VERCEL env var automatically, so
    # we point BACKUP_DIR at /tmp/backups only in that environment; local/
    # Render behavior (relative "backups" folder) is unchanged.
    BACKUP_DIR = os.environ.get(
        "BACKUP_DIR",
        "/tmp/backups" if os.environ.get("VERCEL") else "backups",
    )