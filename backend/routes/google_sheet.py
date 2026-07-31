"""
routes/google_sheet.py — redirects the user to their own tab in the
shared Google Sheet (creating the tab if it doesn't exist yet). Moved
verbatim from app.py.
"""

import os
import json

import gspread
from google.oauth2.service_account import Credentials
from flask import jsonify
from flask_login import current_user

from extensions import app
from utils.decorators import login_required
from normal_sync import CREDENTIALS_FILE, SHEET_ID


@app.route("/my_sheet")
@login_required
def my_sheet():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    if os.getenv("GOOGLE_SERVICE_JSON"):
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(service_info, scopes=scope)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)

    gc = gspread.authorize(creds)

    spreadsheet = gc.open_by_key(SHEET_ID)

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

    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={gid}"

    return jsonify({"success": True, "url": url})
