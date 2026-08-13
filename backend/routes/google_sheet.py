"""
routes/google_sheet.py — "My Sheet": jumps the user to their own tab in
their COHORT YEAR's Google Sheet (PHASE 2 — creating the tab if it
doesn't exist yet). Moved verbatim from app.py, then updated for the
year-wise architecture: there is no more single shared SHEET_ID, every
year has its own spreadsheet (services/year_sheet_service.py).
"""

import logging

from flask import jsonify, request
from flask_login import current_user
import gspread

from extensions import app
from utils.decorators import login_required, admin_required
from utils.security import sanitize
from services.mentor_sheet_sync import MENTOR_SHEET_TAB
from services.year_sheet_service import (
    get_sheet_id_for_year, get_gspread_client, is_year_configured,
)

logger = logging.getLogger(__name__)


def _open_spreadsheet_or_error(sheet_id, year):
    """Shared open-the-spreadsheet-and-turn-any-failure-into-a-clear-
    JSON-message logic for both /admin/master_sheet and /my_sheet.
    Before this, any gspread/auth failure here fell through as an
    unhandled 500 with no JSON body, so the frontend just showed the
    generic "Could not open master sheet." fallback with no way to
    tell what actually went wrong. Returns (spreadsheet, None) on
    success, or (None, (json_response, status_code)) on failure —
    callers just `if err: return err`.
    """
    if not sheet_id:
        return None, (jsonify({
            "success": False,
            "message": f"No Google Sheet is configured for year '{year}' yet. "
                       "Add its spreadsheet ID (or paste the sheet's URL) in the "
                       "mentor dashboard's Year / Cohort Sheets panel."
        }), 409)
    try:
        gc = get_gspread_client()
    except Exception as e:
        logger.exception("Failed to build gspread client for year %s", year)
        return None, (jsonify({
            "success": False,
            "message": f"Google Sheets credentials aren't set up correctly on the server "
                       f"({e.__class__.__name__}). Check GOOGLE_SERVICE_JSON."
        }), 500)
    try:
        spreadsheet = gc.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        logger.warning("Google Sheet open failed for year %s: SpreadsheetNotFound (id=%s)", year, sheet_id)
        return None, (jsonify({
            "success": False,
            "message": f"No spreadsheet found for year '{year}' with ID '{sheet_id}'. "
                       "Double-check the spreadsheet ID/URL saved for this year, and make "
                       "sure it hasn't been deleted or moved."
        }), 404)
    except gspread.exceptions.APIError as e:
        logger.exception("Google Sheets API error opening spreadsheet for year %s", year)
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        return None, (jsonify({
            "success": False,
            "message": (
                f"Google rejected the request for year '{year}'"
                + (f": {detail}" if detail else ".")
                + " This is almost always the service account not having Editor/Viewer "
                  "access on that spreadsheet — share the sheet with the service "
                  "account's email and try again."
            )
        }), 502)
    except Exception as e:
        logger.exception("Unexpected error opening spreadsheet for year %s", year)
        return None, (jsonify({
            "success": False,
            "message": f"Could not open the spreadsheet for year '{year}' ({e.__class__.__name__}: {e})."
        }), 500)
    return spreadsheet, None


@app.route("/admin/master_sheet")
@login_required
@admin_required
def admin_master_sheet():
    """
    'My Sheet' button for the admin/mentor page — jumps straight to the
    roster tab (MENTOR_SHEET_TAB, e.g. "SkillRack Sir Class Track") of
    the YEAR the mentor currently has selected, instead of a per-student
    tab, so an admin doesn't have to hunt for it manually. A mentor can
    request any *configured* year (mentors manage every year) — this is
    still validated against list_configured_years() rather than trusted
    blindly, same reasoning as every other mentor/<year> endpoint.
    """
    year = sanitize(request.args.get("year", ""), 16)
    if not year or not is_year_configured(year):
        return jsonify({"success": False, "message": "Select a valid, configured year first."}), 400

    sheet_id = get_sheet_id_for_year(year)
    spreadsheet, err = _open_spreadsheet_or_error(sheet_id, year)
    if err:
        return err

    try:
        worksheet = spreadsheet.worksheet(MENTOR_SHEET_TAB)
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={worksheet.id}"
    except gspread.exceptions.WorksheetNotFound:
        # Roster tab not found under the configured name — send the admin
        # to the spreadsheet's first tab instead of failing outright.
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    return jsonify({"success": True, "url": url, "tab": MENTOR_SHEET_TAB, "year": year})


@app.route("/my_sheet")
@login_required
def my_sheet():
    """
    A student's own worksheet tab, inside THEIR year's spreadsheet only.
    SECURITY: `year` comes ONLY from current_user.cohort_year (the
    authenticated session's own DB row) — there is deliberately no year
    or sheet_id accepted from query params/body here, so a student can
    never request another year's sheet by changing a request. If the
    student is an admin, admins get taken to /admin/master_sheet's
    behaviour instead isn't wired here — admins use the mentor page.
    """
    year = (getattr(current_user, "cohort_year", "") or "").strip()
    if not year:
        return jsonify({
            "success": False,
            "message": "You're not assigned to a year/cohort yet. Ask your mentor to assign one — your sheet will be available right after."
        }), 409

    sheet_id = get_sheet_id_for_year(year)
    spreadsheet, err = _open_spreadsheet_or_error(sheet_id, year)
    if err:
        return err

    # user oda tab name (example: "example")
    try:
        worksheet = spreadsheet.worksheet(current_user.username)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=current_user.username,
            rows=1000,
            cols=20
        )

    gid = worksheet.id
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"

    return jsonify({"success": True, "url": url, "year": year})
