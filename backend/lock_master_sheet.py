"""
lock_master_sheet.py — one-time, manually-run script for requirement (i):
"only the admin's Gmail can access the [master roster] sheet."

WHY THIS IS A STANDALONE SCRIPT, NOT AUTOMATIC
-------------------------------------------------
This changes real sharing permissions on your live Google Sheet (removing
"anyone with the link" access, if that's currently on, and locking edits
on the roster tab to just you + the service account). That's not
something that should run silently on every deploy or every request — a
wrong ADMIN_GMAIL value here could lock you out of your own sheet, or
strip access from people who still need it (other mentors, etc). So:
run it yourself, once, and check the sheet afterwards.

I have no network access to the Google Sheets API from where I'm working,
so this has NOT been tested against your real spreadsheet — only checked
for correct gspread/Drive API usage. Please run it once locally / in a
one-off shell on your server, then open the sheet as a *different*
Google account to confirm it's actually locked down before trusting it.

USAGE
-----
    python lock_master_sheet.py you@gmail.com

This will:
  1. List everyone currently with access to the spreadsheet (prints it,
     so you can check nothing unexpected is already shared).
  2. Make sure ADMIN_GMAIL has "writer" access to the file.
  3. Remove "anyone with the link" access if that permission exists.
  4. Add a protected range over the master roster tab (MENTOR_SHEET_TAB)
     restricted to [ADMIN_GMAIL, service-account email] — so even other
     people who still have some access to the *file* (e.g. individual
     student tabs, which are separately protected per-user already by
     sheet_protect.py) cannot edit the roster tab specifically.

It deliberately does NOT touch per-student tab sharing/protection — that
is already handled by sheet_protect.py at signup / sync time. This script
only tightens the master roster tab and the file's own general sharing.
"""

import sys
import os
import json

import gspread
from google.oauth2.service_account import Credentials

from normal_sync import CREDENTIALS_FILE, SHEET_ID
from services.mentor_sheet_sync import MENTOR_SHEET_TAB
from sheet_protect import get_service_account_email


def _client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.getenv("GOOGLE_SERVICE_JSON"):
        info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(info, scopes=scope)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return gspread.authorize(creds)


def main(admin_email):
    if "@" not in admin_email:
        print(f"'{admin_email}' doesn't look like an email address — aborting.")
        return

    gc = _client()
    ss = gc.open_by_key(SHEET_ID)
    service_email = get_service_account_email(CREDENTIALS_FILE)

    print(f"\nSpreadsheet: {ss.title}")
    print(f"Service account: {service_email or '(not found)'}\n")

    print("Current access list:")
    perms = ss.list_permissions()
    for p in perms:
        print(f"  - {p.get('emailAddress', p.get('type', '?'))}: {p.get('role')}")

    # 2) Ensure admin has writer access.
    existing_emails = {p.get("emailAddress", "").lower() for p in perms}
    if admin_email.lower() not in existing_emails:
        ss.share(admin_email, perm_type="user", role="writer", notify=False)
        print(f"\nShared spreadsheet with {admin_email} (writer).")
    else:
        print(f"\n{admin_email} already has access.")

    # 3) Remove "anyone with the link" access, if present.
    for p in perms:
        if p.get("type") == "anyone":
            try:
                ss.client.session.delete(
                    f"https://www.googleapis.com/drive/v3/files/{SHEET_ID}/permissions/{p['id']}"
                )
                print("Removed 'anyone with the link' access.")
            except Exception as e:
                print(f"Could not remove link-sharing permission automatically: {e}")
                print("Remove it manually: Share button -> General access -> Restricted.")

    # 4) Lock the roster tab itself to admin + service account only.
    try:
        ws = ss.worksheet(MENTOR_SHEET_TAB)
    except gspread.exceptions.WorksheetNotFound:
        print(f"\nTab '{MENTOR_SHEET_TAB}' not found — set MENTOR_SHEET_TAB env var "
              f"to your roster tab's real name and re-run.")
        return

    body = {
        "requests": [{
            "addProtectedRange": {
                "protectedRange": {
                    "range": {"sheetId": ws.id},
                    "description": f"Roster — {admin_email} only",
                    "warningOnly": False,
                    "editors": {"users": [e for e in [admin_email, service_email] if e]},
                }
            }
        }]
    }
    try:
        ss.batch_update(body)
        print(f"\nProtected '{MENTOR_SHEET_TAB}' — only {admin_email} and the service "
              f"account can edit it now.")
    except Exception as e:
        print(f"\nCould not add protected range (it may already exist): {e}")

    print("\nDone. Open the sheet as a *different* Google account to confirm access "
          "is actually restricted before relying on this.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python lock_master_sheet.py you@gmail.com")
        sys.exit(1)
    main(sys.argv[1])
