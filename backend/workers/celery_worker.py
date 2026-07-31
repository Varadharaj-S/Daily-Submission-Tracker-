"""
workers/celery_worker.py — placeholder for Phase 3.

celery and redis are NOT currently in requirements.txt, and no Redis
instance is assumed to be running anywhere in this deployment. Rather than
scaffold a `Celery(__name__)` app that would fail at import time (no
broker configured) or silently do nothing, this file is left as a marker
for where that work goes.

What actually runs background work today: workers/sync_worker.py
(threading.Thread, started from app.py on boot) and the various
threading.Thread(daemon=True) calls still inline in routes/sync.py and
routes/admin.py (import, admin sync-all, etc). That's a real, working
job queue for a single-process deployment — moving it to Celery + Redis
buys you multi-process/multi-machine workers and job retries/visibility,
at the cost of running and monitoring two more services. Worth doing once
the single-process thread model is actually the bottleneck (see the
Phase 3 discussion in the DSA Tracker v4 proposal), not before.

When that's ready, this becomes:

    from celery import Celery
    celery_app = Celery(__name__, broker=os.environ["REDIS_URL"])

    @celery_app.task
    def sync_user_task(user_id): ...

and workers/sync_worker.py's threading.Thread calls get replaced with
`sync_user_task.delay(user_id)`.
"""
