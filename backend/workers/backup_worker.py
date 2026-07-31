"""
workers/backup_worker.py — pg_dump/psql backup & restore.

Extracted from the /admin/backup and /admin/restore routes in the original
app.py so the logic has one implementation instead of two near-duplicates.
Behavior is unchanged: same commands, same file naming, same DATABASE_URL
handling.
"""

import os
import subprocess
from datetime import datetime

from config import Config


def run_backup():
    """Dump the database to BACKUP_DIR. Returns (ok, message_or_path)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(Config.BACKUP_DIR, f"backup_{ts}.sql")
    db_url = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
    if not db_url:
        return False, "DATABASE_URL is not set — cannot back up."
    try:
        with open(dst, "wb") as f:
            subprocess.run(["pg_dump", db_url], stdout=f, check=True)
        return True, dst
    except Exception as e:
        return False, str(e)


def run_restore(fname):
    """Restore from a previously created backup file in BACKUP_DIR.
    Takes a pre-restore snapshot first, same as the original route did.
    Returns (ok, message)."""
    if not fname or ".." in fname or "/" in fname:
        return False, "Invalid file."
    src = os.path.join(Config.BACKUP_DIR, fname)
    if not os.path.exists(src):
        return False, "File not found."
    db_url = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
    if not db_url:
        return False, "DATABASE_URL is not set — cannot restore."
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pre_restore_dst = os.path.join(Config.BACKUP_DIR, f"pre_restore_{ts}.sql")
    try:
        with open(pre_restore_dst, "wb") as f:
            subprocess.run(["pg_dump", db_url], stdout=f, check=True)
        with open(src, "rb") as f:
            subprocess.run(["psql", db_url], stdin=f, check=True)
        return True, f"Restored from {fname}."
    except Exception as e:
        return False, str(e)


def list_backups():
    return sorted([f for f in os.listdir(Config.BACKUP_DIR) if f.endswith(".sql")], reverse=True)
