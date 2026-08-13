"""
database/seed.py — convenience script for a fresh environment.

Runs the same schema-creation calls the app already makes on startup
(init_db creates all core tables + the default admin user; ensure_tracker_schema
creates and seeds the Daily Tracker sheets). There was no separate seed
script in the original app — this just gives you a way to run that setup
without starting the whole Flask app.

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/dsa_tracker"
    export ADMIN_INIT_PASSWORD="choose-a-strong-password"   # omit to skip creating the default admin
    python database/seed.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import init_db, ensure_db_columns, ensure_year_schema  # noqa: E402
from services.tracker_service import ensure_tracker_schema  # noqa: E402


def main():
    init_db()
    ensure_db_columns()
    ensure_year_schema()
    ensure_tracker_schema()
    admin_note = ("default admin 'admin' created with the ADMIN_INIT_PASSWORD you set"
                  if os.environ.get("ADMIN_INIT_PASSWORD")
                  else "no ADMIN_INIT_PASSWORD set — default admin was NOT created")
    print(f"Database initialized: core tables, {admin_note}, "
          "and the default Daily Tracker sheets.")


if __name__ == "__main__":
    main()
