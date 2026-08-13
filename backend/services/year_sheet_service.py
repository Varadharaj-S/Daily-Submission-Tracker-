"""
services/year_sheet_service.py — PHASE 2 year-wise architecture.

Before Phase 2, every sync module (normal_sync.py, bot_sheet_sync.py,
contest/contest_sheet.py, services/mentor_sheet_sync.py,
lock_master_sheet.py, routes/google_sheet.py) hardcoded the same
constant: SHEET_ID = "1vucuD_...". One spreadsheet, every student's
worksheet tab living inside it together.

That's gone. There is now one spreadsheet PER YEAR/COHORT (same
per-student-worksheet-tab layout inside each one) and this module is
the ONLY place that maps year -> spreadsheet ID. Nothing else should
read the year_sheets table directly or hardcode a spreadsheet ID.

SECURITY NOTE: the `year` passed in here must already be a *trusted*
value by the time it reaches this module:
  - for a student-facing request: current_user.cohort_year, read from
    the authenticated session/DB row — never a year/sheet_id taken
    from query params, form body, or JSON, even if the student is the
    one submitting it.
  - for a mentor-facing request: any configured year is fine, since
    mentors are allowed to manage every year — but it should still be
    validated against list_configured_years() rather than trusted
    blindly, so a typo doesn't silently create a bad mapping.
"""

from datetime import datetime

from database.db import get_db


def get_sheet_id_for_year(year):
    """Returns the spreadsheet ID configured for this year, or None if
    no year was given or the mentor hasn't configured a sheet for it
    yet. Callers MUST treat None as "no sheet available" and fail
    clearly — never fall back to a shared/default spreadsheet ID."""
    if not year:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT spreadsheet_id FROM year_sheets WHERE year=?", (str(year),)
        ).fetchone()
    return row["spreadsheet_id"] if row else None


def set_sheet_id_for_year(year, spreadsheet_id):
    """Mentor-only: create or update the spreadsheet mapped to a year."""
    year = (str(year) if year is not None else "").strip()
    spreadsheet_id = (spreadsheet_id or "").strip()
    if not year:
        raise ValueError("year is required")
    if not spreadsheet_id:
        raise ValueError("spreadsheet_id is required")
    with get_db() as db:
        db.execute("""
            INSERT INTO year_sheets (year, spreadsheet_id, updated_at)
            VALUES (?,?,?)
            ON CONFLICT (year) DO UPDATE
                SET spreadsheet_id = EXCLUDED.spreadsheet_id,
                    updated_at     = EXCLUDED.updated_at
        """, (year, spreadsheet_id, datetime.now().isoformat()))
        db.commit()


def delete_year_sheet(year):
    if not year:
        return
    with get_db() as db:
        db.execute("DELETE FROM year_sheets WHERE year=?", (str(year),))
        db.commit()


def list_year_sheets():
    """Every configured year -> spreadsheet mapping, for the mentor UI."""
    with get_db() as db:
        rows = db.execute(
            "SELECT year, spreadsheet_id, updated_at FROM year_sheets ORDER BY year"
        ).fetchall()
    return [dict(r) for r in rows]


def list_configured_years():
    return [r["year"] for r in list_year_sheets()]


def is_year_configured(year):
    return bool(year) and str(year) in list_configured_years()


def get_gspread_client():
    """Shared credential-loading logic, deduplicated from the five
    places that each had their own copy of this exact block. Prefers
    GOOGLE_SERVICE_JSON (env var, used in production); falls back to
    the local service-account file for local dev."""
    import os
    import json
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.getenv("GOOGLE_SERVICE_JSON"):
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(service_info, scopes=scope)
    else:
        from normal_sync import CREDENTIALS_FILE
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return gspread.authorize(creds)


def open_year_spreadsheet(year):
    """Resolves `year` to a spreadsheet ID and opens it. Raises
    ValueError (not a silent None) if the year has no sheet configured
    yet, so callers surface a clear "ask the mentor to configure this
    year" message instead of accidentally falling through to some
    other sheet."""
    sheet_id = get_sheet_id_for_year(year)
    if not sheet_id:
        raise ValueError(
            f"No Google Sheet is configured for year '{year}' yet. "
            "Ask your mentor to set one up in the mentor dashboard."
        )
    client = get_gspread_client()
    return client.open_by_key(sheet_id)
