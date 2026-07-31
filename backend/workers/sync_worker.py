"""
workers/sync_worker.py — starts the background auto-sync scheduler.

This is the same threading.Thread(daemon=True) approach app.py used at the
bottom of the file, just given a proper home. It is NOT a Celery worker —
this project doesn't have Celery/Redis in requirements.txt yet, so rather
than scaffold an unused `celery_worker.py` that can't actually run, this
keeps using the working thread-based scheduler until Phase 3 introduces a
real task queue.
"""

import threading

from services.scheduler_service import run_scheduler


def start_sync_worker():
    """Start the auto-sync scheduler loop in a background daemon thread."""
    threading.Thread(target=run_scheduler, daemon=True).start()
