"""
routes/internal.py — Internal endpoints only reachable from within the
persistent worker deployment (Render — see render.yaml / Procfile), or via
a shared secret. NOT exposed to the public.

NOTE: the Import LC delegation endpoints that used to live here
(/internal/trigger_import, /internal/import_status/<id>) have been removed.
/import_lc (routes/sync.py) now runs synchronously in-request instead of
being handed off to this worker in the background — see routes/sync.py's
module docstring.

The cron delegation endpoints below are unrelated to Import LC and are
unchanged.

Security: every request must carry the correct Config.INTERNAL_TASK_SECRET
value in the X-Internal-Secret header. Set INTERNAL_TASK_SECRET to the same
long random string in both your Vercel and Render environment variable
panels (see config.py — this is the single source of truth for the name).
"""

import threading

from flask import request, jsonify

from extensions import app
from config import Config


def _require_internal_secret():
    """Returns (True, None) if the request is authorized, (False, response) otherwise."""
    secret = Config.INTERNAL_TASK_SECRET
    if not secret:
        # No secret configured → deny all; don't let this endpoint be open.
        return False, (jsonify({"ok": False, "error": "INTERNAL_TASK_SECRET not configured"}), 403)

    provided = request.headers.get("X-Internal-Secret", "")
    if provided != secret:
        return False, (jsonify({"ok": False, "error": "Forbidden"}), 403)

    return True, None


# ── Cron delegation endpoints ─────────────────────────────────────────────────
# Vercel Cron routes POST here so the actual long-running work executes on
# the Render persistent backend, not inside a Vercel serverless function.

@app.route("/internal/cron/daily-sync", methods=["POST"])
def internal_cron_daily_sync():
    """Triggered by Vercel cron → routes/cron.py → here. Runs scheduler.main()
    in a background thread so this endpoint returns fast enough for the caller."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    def _run():
        try:
            from scheduler import main as run_daily_sync
            run_daily_sync()
        except Exception as e:
            print(f"[internal cron daily-sync] error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Daily sync started in background"})


@app.route("/internal/cron/contest-sync", methods=["POST"])
def internal_cron_contest_sync():
    """Triggered by Vercel cron → routes/cron.py → here."""
    ok, err = _require_internal_secret()
    if not ok:
        return err

    def _run():
        try:
            from contest_scheduler import main as run_contest_sync
            run_contest_sync()
        except Exception as e:
            print(f"[internal cron contest-sync] error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Contest sync started in background"})
