"""
routes/google_sheet.py — "My Sheet": jumps the user to their own tab in
their COHORT YEAR's Google Sheet (PHASE 2 — creating the tab if it
doesn't exist yet). Moved verbatim from app.py, then updated for the
year-wise architecture: there is no more single shared SHEET_ID, every
year has its own spreadsheet (services/year_sheet_service.py).
"""

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
    gc = get_gspread_client()
    spreadsheet = gc.open_by_key(sheet_id)

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
    if not sheet_id:
        return jsonify({
            "success": False,
            "message": f"No Google Sheet has been configured for {year} yet. Ask your mentor to set one up."
        }), 409

    gc = get_gspread_client()
    spreadsheet = gc.open_by_key(sheet_id)

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
