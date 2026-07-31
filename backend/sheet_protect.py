"""
sheet_protect.py
─────────────────
Fixes requirement (iii): one main Google Sheet, one tab per username.
Right now every user's tab sits wide open in that shared spreadsheet — any
user who has edit access to the spreadsheet can open/edit anyone else's tab.

This module:
  1. Shares the spreadsheet with a user's *signup email* (so they can open
     their own tab at all).
  2. Adds a Protected Range covering that user's entire tab, restricted to
     ONLY that user's email + the service account (so nobody else who has
     access to the spreadsheet can edit — or in most Sheets UIs, even see —
     the contents of somebody else's tab).

Safe to call every time a tab is created/found — it no-ops if the
protection / share already exists.
"""

import json
import os


def get_service_account_email(credentials_file="valiant-splicer-489013-q2-40d3ac23a2d8.json"):
    """Returns the service account's own email, so it can always be kept as
    an editor of every protected range (otherwise the bot itself would get
    locked out of writing to the sheet it just protected)."""
    try:
        if os.getenv("GOOGLE_SERVICE_JSON"):
            info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        else:
            with open(credentials_file) as f:
                info = json.load(f)
        return info.get("client_email", "")
    except Exception:
        return ""


def share_spreadsheet_with_user(spreadsheet, user_email, role="writer"):
    """Give the user access to the shared spreadsheet file itself.
    Without this, the protected-range editor list below is meaningless —
    they'd never be able to open the file in the first place."""
    if not user_email or "@" not in user_email:
        return
    try:
        existing = {p.get("emailAddress", "").lower() for p in spreadsheet.list_permissions()}
        if user_email.lower() in existing:
            return
        spreadsheet.share(user_email, perm_type="user", role=role, notify=False)
    except Exception as e:
        print(f"[sheet_protect] Could not share spreadsheet with {user_email}: {e}")


def protect_user_worksheet(spreadsheet, worksheet, user_email, service_account_email=None):
    """Restrict edits on `worksheet` to only [user_email, service_account_email].
    Everyone else who has access to the wider spreadsheet loses edit rights
    on this specific tab. Idempotent — skips if a matching protection
    already exists on this sheet."""
    if not user_email or "@" not in user_email:
        print(f"[sheet_protect] Skipping protection for '{worksheet.title}' — no valid signup email on file.")
        return

    editors = [e for e in [user_email, service_account_email] if e]

    try:
        meta = spreadsheet.fetch_sheet_metadata()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("sheetId") != worksheet.id:
                continue
            for pr in sheet.get("protectedRanges", []):
                pr_editors = set((pr.get("editors") or {}).get("users") or [])
                if pr_editors and pr_editors.issubset(set(editors) | {service_account_email}):
                    # Already protected for this user — nothing to do.
                    return

        body = {
            "requests": [{
                "addProtectedRange": {
                    "protectedRange": {
                        "range": {"sheetId": worksheet.id},
                        "description": f"Private tab for {user_email}",
                        "warningOnly": False,
                        "editors": {"users": editors},
                    }
                }
            }]
        }
        spreadsheet.batch_update(body)
        print(f"[sheet_protect] Tab '{worksheet.title}' protected — only {editors} can edit it.")
    except Exception as e:
        print(f"[sheet_protect] Could not protect '{worksheet.title}': {e}")


def secure_user_tab(spreadsheet, worksheet, user_email, credentials_file="valiant-splicer-489013-q2-40d3ac23a2d8.json"):
    """Convenience: share + protect in one call."""
    
    service_email = get_service_account_email(credentials_file)
    share_spreadsheet_with_user(spreadsheet, user_email)
    protect_user_worksheet(spreadsheet, worksheet, user_email, service_email)
