"""
sync/sync_service.py
ONE clean sync flow — replaces bot.py + automation_updated.py + normal_sync.py
Supports: incremental sync, Google Sheets write, PostgreSQL write
"""
import json, time
from datetime import datetime

from sync.cf_service  import fetch_cf
from sync.lc_service  import fetch_lc_cookie, fetch_lc_public
from sync.ac_service  import fetch_ac

# ── Google Sheets ────────────────────────────────────────────────────────────
try:
    import gspread
    from google.oauth2.service_account import Credentials
    _SHEETS_OK = True
except ImportError:
    _SHEETS_OK = False

CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"
SHEET_ID         = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"


def _get_sheet(username):
    if not _SHEETS_OK:
        raise RuntimeError("gspread not installed")
    import os
    import json
    from google.oauth2.service_account import Credentials

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if os.getenv("GOOGLE_SERVICE_JSON"):
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(service_info, scopes=scope)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    client = gspread.authorize(creds)
    ss = client.open_by_key(SHEET_ID)
    try:
        return ss.worksheet(username)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=username, rows="5000", cols="10")
        ws.append_row(["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"])
        return ws


def create_user_sheet(email, username):
    """Create dedicated Google Sheet for user. Returns (sheet_id, message)."""
    try:
        if not _SHEETS_OK:
            return None, "gspread not installed"
        import os
        import json
        from google.oauth2.service_account import Credentials

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        if os.getenv("GOOGLE_SERVICE_JSON"):
            service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
            creds = Credentials.from_service_account_info(service_info, scopes=scope)
        else:
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        client = gspread.authorize(creds)
        ss = client.create(f"DSA Tracker — {username}")
        ws = ss.get_worksheet(0)
        ws.update_title(username)
        ws.append_row(["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"])
        if email:
            ss.share(email, perm_type="user", role="writer")
        return ss.id, "created"
    except Exception as e:
        return None, str(e)


from datetime import datetime

def _to_sheet_row(sub):
    """Convert a submission dict to [date, hyperlink title, url, diff, platform, topic, 1]"""

    title = sub["problem_name"].replace('"', '""')
    url = sub["problem_url"]

    date = datetime.strptime(
        sub["solved_date"],
        "%Y-%m-%d"
    ).strftime("%Y-%m-%d")

    return [
        date,
        f'=HYPERLINK("{url}", "{title}")',
        url,
        sub["difficulty"],
        sub["platform"],
        sub.get("tags", "General"),
        1,
    ]


def sync_user_data(user, get_db, full_import=False):
    """
    Main sync entry point.
    - Incremental by default (only fetches since last_sync).
    - full_import=True forces full history fetch (used on first LC import).
    - Writes to SQLite + Google Sheets.
    """
    uid        = user["id"]
    username   = (user.get("username") or f"user_{uid}").strip()
    cf_handle  = (user.get("cf_handle") or "").strip()
    lc_handle  = (user.get("lc_handle") or "").strip()
    lc_cookie  = (user.get("lc_session_cookie") or "").strip()
    ac_handle  = (user.get("ac_handle") or "").strip()
    last_sync  = (user.get("last_sync") or "").strip() if not full_import else None

    # Parse enabled platforms
    try:
        enabled = json.loads(user.get("enabled_platforms") or '["Codeforces","LeetCode","AtCoder"]')
    except Exception:
        enabled = ["Codeforces", "LeetCode", "AtCoder"]

    print(f"\n🔄 Syncing [{username}] — incremental={bool(last_sync)} platforms={enabled}")

    all_subs = []

    if "Codeforces" in enabled and cf_handle:
        all_subs.extend(fetch_cf(cf_handle, last_sync=last_sync))

    if "LeetCode" in enabled and lc_handle:
        if lc_cookie and full_import:
            all_subs.extend(fetch_lc_cookie(lc_handle, lc_cookie, last_sync=None))
        elif lc_cookie:
            all_subs.extend(fetch_lc_cookie(lc_handle, lc_cookie, last_sync=last_sync))
        else:
            all_subs.extend(fetch_lc_public(lc_handle, last_sync=last_sync))

    if "AtCoder" in enabled and ac_handle:
        all_subs.extend(fetch_ac(ac_handle, last_sync=last_sync))

    if not all_subs:
        return {"success": False, "message": "No new data fetched", "new_count": 0}

    # ── Write to PostgreSQL ──────────────────────────────────────────────────
    conn = get_db()
    cur  = conn.cursor()
    new_count = 0
    new_rows  = []

    for sub in all_subs:
        cur.execute("""
            INSERT INTO submissions
            (user_id, problem_name, problem_id, problem_url, platform,
             difficulty, tags, solved_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, platform, problem_id) DO NOTHING
        """, (uid, sub["problem_name"], sub["problem_id"], sub["problem_url"],
              sub["platform"], sub["difficulty"], sub.get("tags",""), sub["solved_date"]))
        if cur.rowcount > 0:
            new_count += 1
            new_rows.append(sub)

    conn.commit()
    conn.close()

    # ── Write to Google Sheets ───────────────────────────────────────────────
    if new_rows:
        try:
            ws = _get_sheet(username)
            ws.append_rows(
                    [_to_sheet_row(s) for s in new_rows],
                    value_input_option="USER_ENTERED"
                )

# Header bold
            ws.format(
    "A1:G1",
    {
        "textFormat": {"bold": True},
        "horizontalAlignment": "CENTER"
    }
)

# Freeze header
            ws.freeze(rows=1)

# Add filter
            ws.set_basic_filter()

# Sort by Date column (A)
            ws.sort((1, "des"))
            print(f"📄 Added {len(new_rows)} rows to sheet tab '{username}'")
        except Exception as e:
            print(f"[Sheets] Error: {e}")

    # ── Auto-detect mentor task completion ───────────────────────────────────
    _auto_complete_mentor_tasks(uid, all_subs)

    return {
        "success": True,
        "message": f"{new_count} new problems added",
        "new_count": new_count,
    }


def _auto_complete_mentor_tasks(user_id, synced_subs):
    """
    Auto-mark mentor assignments as completed if the problem_url
    matches a newly synced submission.
    """
    if not synced_subs:
        return
    synced_urls = {s["problem_url"] for s in synced_subs}
    try:
        from database.db import get_db as _pg_get_db
        conn = _pg_get_db()
        cur  = conn.cursor()
        cur.execute(
            "SELECT id, problem_url FROM mentor_assignments WHERE user_id=? AND completed=0",
            (user_id,)
        )
        for row in cur.fetchall():
            mid, url = row["id"], row["problem_url"]
            if url and url in synced_urls:
                cur.execute(
                    "UPDATE mentor_assignments SET completed=1 WHERE id=?", (mid,)
                )
                print(f"[Mentor] Auto-completed task {mid} for user {user_id}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Mentor AutoComplete] {e}")
