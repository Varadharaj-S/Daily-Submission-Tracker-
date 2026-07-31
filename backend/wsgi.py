"""
wsgi.py — entry point for production servers (gunicorn wsgi:app).

Deliberately just re-exports `app` from app.py — importing app.py also runs
init_db() and registers every route module, same as running `python app.py`
would, just without the __main__ dev-server block at the bottom.
"""

from app import app  # noqa: F401
