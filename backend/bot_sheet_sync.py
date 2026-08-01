import requests
import pandas as pd
import gspread
import time
import sys
import json
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
# selenium removed — extension handles cookie capture
import time
from database.db import get_db
from datetime import datetime
from google.oauth2.service_account import Credentials

# ===============================
# CONFIG
# ===============================

SHEET_ID         = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"
CREDENTIALS_FILE ="valiant-splicer-489013-q2-40d3ac23a2d8.json"

# ===============================
# LOAD USER FROM DB USING user_id arg
# ===============================

user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1

conn   = get_db()
cursor = conn.cursor()
cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
user_row = cursor.fetchone()
conn.close()

if not user_row:
    print(f"❌ No user found with id={user_id}")
    sys.exit(1)

user_row   = dict(user_row)
CF_USER    = (user_row.get("cf_handle") or "").strip()
LC_USER    = (user_row.get("lc_handle") or "").strip()
AC_USER    = (user_row.get("ac_handle") or "").strip()
USERNAME   = (user_row.get("username")  or f"user_{user_id}").strip()
USER_EMAIL = (user_row.get("email")     or "").strip()

# LeetCode session/csrf from user's saved settings
LEETCODE_SESSION = (user_row.get("lc_session_cookie") or "").strip()
LEETCODE_CSRF    = (user_row.get("lc_csrf_token")     or "").strip()

print(f"User: {USERNAME}  CF={CF_USER or '(none)'}  LC={LC_USER or '(none)'}  AC={AC_USER or '(none)'}")

# NOTE: There used to be a fallback here that loaded LEETCODE_SESSION /
# LEETCODE_CSRF from config.json whenever a user had not connected their own
# LeetCode account yet. That meant every user without their own cookie ended
# up importing/syncing under ONE shared (the developer's own) LeetCode
# session — everybody saw the same account's data.
#
# This is intentionally REMOVED. Each user MUST connect their own LeetCode
# account from Settings -> "Connect LeetCode account" first (which saves
# lc_session_cookie / lc_csrf_token for THEIR user id only). If they haven't,
# we stop here instead of silently borrowing someone else's cookie.
if not LEETCODE_SESSION or not LEETCODE_CSRF:
    if LC_USER:
        print(f"No personal LeetCode session/csrf saved for '{USERNAME}'. "
              f"Ask them to go to Settings -> Connect LeetCode account first. "
              f"Skipping LC import for this user (no shared/global fallback).")
    LEETCODE_SESSION = ""
    LEETCODE_CSRF = ""

# ===============================
# LEETCODE DETAILS (difficulty + tags)
# ===============================

def get_leetcode_details(slug, session_obj=None):
    url   = "https://leetcode.com/graphql"
    query = {
        "query": """
        query getQuestion($titleSlug: String!) {
          question(titleSlug: $titleSlug) {
            difficulty
            topicTags { name }
          }
        }
        """,
        "variables": {"titleSlug": slug}
    }
    try:
        if session_obj:
            res = session_obj.post(url, json=query, timeout=60)
        else:
            res = requests.post(url, json=query,
                                headers={"Content-Type": "application/json",
                                         "Referer": "https://leetcode.com",
                                         "User-Agent": "Mozilla/5.0"},
                                timeout=60)
        if res.status_code != 200:
            return "Unknown", "General"
        data       = res.json()
        question   = data["data"]["question"]
        difficulty = question["difficulty"]
        tags       = question["topicTags"]
        topic      = ", ".join(tag["name"] for tag in tags) or "General"
        return difficulty, topic
    except:
        return "Unknown", "General"

# ===============================
# GOOGLE SHEETS — per-user tab
# ===============================

# 🔒 Multiple users can press "Import LC" / auto-sync at the same exact time.
# Each runs as its own subprocess, so without a lock they'd all hit the same
# shared spreadsheet's API at once (race conditions on tab creation, 429
# rate-limit errors, interleaved writes). This file lock makes concurrent
# runs queue one-after-another against the spreadsheet instead of colliding —
# each user's own LeetCode cookie/session is untouched by this, it only
# serializes the Google Sheets read/write portion.
import atexit
from filelock import FileLock

SHEET_LOCK_PATH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "sheet_sync.lock")

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
    


# Get or create a tab named after this user
# try:
#     user_sheet = spreadsheet.worksheet(USERNAME)
#     print(f"Found existing sheet tab '{USERNAME}' ✅")
# except gspread.exceptions.WorksheetNotFound:
#     user_sheet = spreadsheet.add_worksheet(title=USERNAME, rows="5000", cols="10")
#     user_sheet.append_row(["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"])
#     print(f"Created new sheet tab '{USERNAME}' ✅")

# ── Protect this tab so only THIS user (by their signup email) + the bot's
#    service account can edit it — other users sharing the same main
#    spreadsheet can no longer touch each other's tabs. Safe/idempotent. ──
from sheet_protect import secure_user_tab

# secure_user_tab(spreadsheet, user_sheet, USER_EMAIL, CREDENTIALS_FILE)

#--------------------------------------------------------

# capture_cookie (Selenium) removed — extension handles cookie capture

# ===============================
# LEETCODE SESSION HELPERS
# ===============================
import re  # make sure this is at top

def build_leetcode_session(session_cookie="", csrf_token=""):
    sess = requests.Session()

    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://leetcode.com",
        "Referer": "https://leetcode.com/",
        "X-Requested-With": "XMLHttpRequest",
    })

    # 🔥 LEETCODE SESSION
    if session_cookie:
        if "LEETCODE_SESSION=" in session_cookie:
            m = re.search(r"LEETCODE_SESSION=([^;]+)", session_cookie)
            if m:
                session_cookie = m.group(1)

        session_cookie = session_cookie.strip()
        sess.cookies.set("LEETCODE_SESSION", session_cookie, domain=".leetcode.com", path="/")

    # 🔥 CSRF
    if csrf_token:
        csrf_token = csrf_token.strip()
        sess.cookies.set("csrftoken", csrf_token, domain=".leetcode.com", path="/")
        sess.headers["X-CSRFToken"] = csrf_token

    # 🔥 Refresh cookies
    try:
        sess.get("https://leetcode.com/", timeout=60)
        refreshed = sess.cookies.get("csrftoken", "")
        if refreshed:
            sess.headers["X-CSRFToken"] = refreshed
    except Exception as e:
        print(f"Could not refresh csrf: {e}")

    return sess


def verify_leetcode_login(sess):
    try:
        r = sess.post(
            "https://leetcode.com/graphql",
            json={"query": "query{userStatus{username isSignedIn}}"},
            timeout=60,
        )
        data = r.json()
        return bool((data.get("data") or {}).get("userStatus", {}).get("isSignedIn")), data
    except Exception as e:
        return False, {"error": str(e)}



all_data = []

# ===============================
# CODEFORCES
# ===============================

if CF_USER:
    print(f"Fetching Codeforces for '{CF_USER}'...")
    cf_url   = f"https://codeforces.com/api/user.status?handle={CF_USER}&count=10000"
    response = requests.get(cf_url).json()

    if response["status"] == "OK":
        for sub in response["result"]:
            if sub.get("verdict") != "OK":
                continue
            problem    = sub["problem"]
            contest_id = problem.get("contestId")
            index      = problem.get("index")
            if not contest_id or not index:
                continue

            problem_link    = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
            submission_link = f"https://codeforces.com/contest/{contest_id}/submission/{sub['id']}"
            title           = problem.get("name", "Unknown")
            title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

            rating          = problem.get("rating")

            if rating is None:
                difficulty = "Medium"
            elif rating < 1200:
                difficulty = "Easy"
            elif rating <= 1800:
                difficulty = "Medium"
            else:
                difficulty = "Hard"

            tags  = problem.get("tags", [])
            topic = ", ".join(tags) if tags else "General"
            date = datetime.fromtimestamp(int(sub["creationTimeSeconds"])).strftime("%Y-%m-%d")

            all_data.append([
    date,
    title_cell,
    submission_link,
    difficulty,
    "Codeforces",
    topic,
    1
])

    print("Codeforces collected ✅")
else:
    print("[CF] No handle set, skipping.")

#----------------------------------------------------------
def fetch_leetcode_cookie_from_db(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT lc_session_cookie, lc_csrf_token 
        FROM users WHERE id=?
    """, (user_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        raise Exception("❌ User not found in DB")

    session_cookie = row["lc_session_cookie"]
    csrf = row["lc_csrf_token"]

    if not session_cookie:
        raise Exception("❌ No LEETCODE_SESSION cookie found")

    if not csrf:
        print("⚠️ CSRF missing (may still work)")

    print("✅ Using stored LeetCode cookie")

    return session_cookie, csrf
# ===============================
# LEETCODE — session cookie based, full history
# ===============================

if LC_USER:
    print(f"Fetching LeetCode for '{LC_USER}'...")

    LC_GQL = "https://leetcode.com/graphql"

    session_cookie, csrf = fetch_leetcode_cookie_from_db(user_id)

    lc_sess = build_leetcode_session(session_cookie, csrf)
    lc_logged_in, login_data = verify_leetcode_login(lc_sess)

    if lc_logged_in:
        print("  LeetCode session verified ✅")
    else:
        print("❌ Still not verified — but continuing anyway (may fetch limited data)")

    # 🔥 If cookie invalid → auto open Selenium
    if not lc_logged_in:
        print("⚠️ Cookie expired → opening browser for login...")

        success = False  # Selenium removed — extension handles reconnect

        if not success:
            print("❌ Login failed")
            sys.exit(1)

        # 🔥 reload new cookie
        session_cookie, csrf = fetch_leetcode_cookie_from_db(user_id)
        lc_sess = build_leetcode_session(session_cookie, csrf)

        lc_logged_in, _ = verify_leetcode_login(lc_sess)

        if not lc_logged_in:
            print("❌ Even after login, verification failed")
            sys.exit(1)

        print("✅ New cookie verified successfully!")
# save in DB
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE users
    SET lc_session_cookie=?, lc_csrf_token=?
    WHERE id=?
    """, (session_cookie, csrf, user_id))

    conn.commit()
    conn.close()

    lc_sess = build_leetcode_session(session_cookie, csrf)

    # Verify login before fetch
    lc_logged_in, login_data = verify_leetcode_login(lc_sess)
    lc_failed = False
    if lc_logged_in:
        print("  LeetCode session verified ✅")
    else:
        lc_failed = True
        print("  LeetCode session NOT verified ⚠️  — cookie may be expired")
        if "error" in login_data:
            print(f"  LeetCode verify error: {login_data['error']}")
        else:
            print(f"  LOGIN CHECK: {login_data}")

    lc_seen   = set()
    lc_offset = 0

    print("🚀 STARTING LEETCODE FETCH")

    while True:

        try:
            r = lc_sess.get(
                "https://leetcode.com/api/submissions/",
                params={"offset": lc_offset, "limit": 20},
                timeout=(5, 10)
            )


        except Exception as e:
            print("❌ REQUEST ERROR =", e)
            break

        if r.status_code == 401:
            lc_failed = True
            print("  LeetCode API returned 401, stopping.")
            break
        if r.status_code == 403:
            lc_failed = True
            print(f"  403 at offset {lc_offset} — cookie expired. Update settings ❌")
            break
        if r.status_code != 200:
            lc_failed = True
            print(f"  LeetCode API returned {r.status_code}, stopping.")
            break

        data = r.json()
        subs = data.get("submissions_dump", [])

        if not subs:
            break

        for sub in subs:
            if sub.get("status_display") != "Accepted":
                continue

            slug = sub.get("title_slug", "")
            if not slug or slug in lc_seen:
                continue
            lc_seen.add(slug)

            ts = sub.get("creationTimeSeconds") or sub.get("timestamp")

            if not ts:
                continue   # skip if missing

            date = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            title = sub.get("title", slug)

            # Use cookie-backed session for details too
            difficulty, topic = get_leetcode_details(slug, session_obj=lc_sess)
            time.sleep(0.35)

            problem_link    = f"https://leetcode.com/problems/{slug}/"
            submission_link = f"https://leetcode.com/submissions/detail/{sub['id']}/"
            title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

            all_data.append([date, title_cell, submission_link, difficulty, "LeetCode", topic, 1])

        if not data.get("has_next", False):
            break

        lc_offset += 20
        time.sleep(0.25)

    print(f"LeetCode collected ✅  ({len(lc_seen)} unique problems)")
else:
    print("[LC] No handle set, skipping.")

# ===============================
# ATCODER
# ===============================

if AC_USER:
    print(f"Fetching AtCoder for '{AC_USER}'...")

    url          = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={AC_USER}&from_second=0"
    submissions  = requests.get(url).json()
    problem_data = requests.get("https://kenkoooo.com/atcoder/resources/problems.json").json()
    problem_map  = {p["id"]: p["title"] for p in problem_data}

    seen = set()
    for sub in submissions:
        if sub["result"] != "AC":
            continue
        problem = sub["problem_id"]
        if problem in seen:
            continue
        seen.add(problem)

        contest       = sub["contest_id"]
        submission_id = sub["id"]
        date  = datetime.fromtimestamp(sub["epoch_second"]).strftime("%Y-%m-%d")
        title         = problem_map.get(problem, problem)

        problem_link    = f"https://atcoder.jp/contests/{contest}/tasks/{problem}"
        submission_link = f"https://atcoder.jp/contests/{contest}/submissions/{submission_id}"
        title_cell = f'=HYPERLINK("{problem_link}", "{title}")'


        if "_a" in problem:
            difficulty, topic = "Easy",    "Implementation"
        elif "_b" in problem:
            difficulty, topic = "Easy",    "Math"
        elif "_c" in problem:
            difficulty, topic = "Medium",  "Greedy"
        elif "_d" in problem:
            difficulty, topic = "Hard",    "Dynamic Programming"
        else:
            difficulty, topic = "Unknown", "General"

        all_data.append([date, title_cell, submission_link, difficulty, "AtCoder", topic, 1])

    print("AtCoder collected ✅")
else:
    print("[AC] No handle set, skipping.")

# ===============================
# DATAFRAME — sort & dedup
# ===============================

if all_data:
    df = pd.DataFrame(all_data, columns=[
        "DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"
    ])
    df = df.drop_duplicates(subset=["DATE", "PROGRAM TITLE", "PLATFORM"])
    df["DATE"] = pd.to_datetime(df["DATE"])   # ✅ keep ISO
    df = df.sort_values(by="DATE")
    df["COUNT"] = df.groupby("DATE")["PROGRAM TITLE"].transform("count")

    # 🔥 keep DB data in YYYY-MM-DD
    db_data = df.copy()
    db_data["DATE"] = db_data["DATE"].dt.strftime("%Y-%m-%d")

    # 🔥 convert only for Google Sheet
    sheet_data = df.copy()
    sheet_data["DATE"] = sheet_data["DATE"].dt.strftime("%d-%m-%Y")

# ===============================
# SAVE TO POSTGRESQL
# ===============================

conn   = get_db()
cursor = conn.cursor()
new_count = 0

for row in db_data.values.tolist():
    problem_url = ""

    if 'HYPERLINK(' in row[1]:
        problem_url = row[1].split('"')[1]

    submission_url = row[2]

    title = (
        row[1].split('", "')[-1].rstrip('")')
        if 'HYPERLINK(' in row[1]
        else row[1]
    )

    if "leetcode.com/problems/" in problem_url:
        problem_id = problem_url.split("/problems/")[-1].split("/")[0]
    elif "codeforces.com/problemset/problem" in problem_url:
        parts = problem_url.split("/")
        problem_id = parts[-2] + "-" + parts[-1]
    elif "atcoder.jp" in problem_url:
        problem_id = problem_url.split("/")[-1]
    else:
        problem_id = problem_url

    cursor.execute("""
INSERT INTO submissions
(
    user_id,
    problem_name,
    problem_id,
    problem_url,
    submission_url,
    platform,
    difficulty,
    tags,
    solved_date
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (user_id, platform, problem_id)
DO NOTHING
""", (
    user_id,
    title,
    problem_id,
    problem_url,
    submission_url,
    row[4],   # platform
    row[3],   # difficulty
    row[5],   # tags
    row[0]    # solved_date
))

    if cursor.rowcount > 0:
        new_count += 1

conn.commit()
conn.close()


print(f"🔥 New problems added to DB: {new_count}")


#-----------------------------------------------------
#Sheet Writhing
#-----------------------------------------------------
def _with_retry(fn, *args, retries=4, **kwargs):
    """Retries a gspread call with exponential backoff on rate-limit/transient
    errors (HTTP 429/500/503), instead of the whole import crashing."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 500, 503) and attempt < retries - 1:
                wait = 2 ** attempt * 2
                print(f"⚠️ Sheets API {status}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise


def write_sheet_import(user_sheet, sheet_data, USERNAME):

    print("📥 Import mode → rebuilding sheet...")

    import time
    from collections import defaultdict

    _with_retry(user_sheet.clear)

    try:
        user_sheet.spreadsheet.batch_update({
            "requests": [{
                "unmergeCells": {
                    "range": {
                        "sheetId": user_sheet.id
                    }
                }
            }]
        })
    except:
        pass

    user_sheet.append_row([
        "DATE",
        "PROGRAM TITLE",
        "LINK",
        "DIFFICULTY",
        "PLATFORM",
        "TOPIC",
        "COUNT"
    ])

    _with_retry(
        user_sheet.append_rows,
        sheet_data.values.tolist(),
        value_input_option="USER_ENTERED"
    )

    print(f"ROWS WRITTEN = {len(sheet_data)}")

    time.sleep(1)

    all_values = user_sheet.get_all_values()

    date_rows = defaultdict(list)

    for row_num, row in enumerate(all_values[1:], start=2):

        if not row:
            continue

        date_val = str(row[0]).strip()

        if date_val:
            date_rows[date_val].append(row_num)

    requests = []

    for date_val, rows in date_rows.items():

        if len(rows) <= 1:
            continue


        # DATE COLUMN
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": user_sheet.id,
                    "startRowIndex": rows[0] - 1,
                    "endRowIndex": rows[-1],
                    "startColumnIndex": 0,
                    "endColumnIndex": 1
                },
                "mergeType": "MERGE_ALL"
            }
        })

        # COUNT COLUMN
        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": user_sheet.id,
                    "startRowIndex": rows[0] - 1,
                    "endRowIndex": rows[-1],
                    "startColumnIndex": 6,
                    "endColumnIndex": 7
                },
                "mergeType": "MERGE_ALL"
            }
        })


    if requests:
        _with_retry(user_sheet.spreadsheet.batch_update, {
            "requests": requests
        })


    conn = get_db()

    conn.execute("""
    UPDATE users
    SET lc_imported = 1
    WHERE id = ?
    """, (user_id,))

    conn.commit()
    conn.close()

    print("✅ lc_imported updated")
    print(f"📥 Import complete for '{USERNAME}' ✅")


# ===============================
# WRITE TO USER'S SHEET TAB
# ===============================
if all_data:
    try:

        print(f"⏳ [{USERNAME}] waiting for sheet-sync turn...")

        with FileLock(SHEET_LOCK_PATH, timeout=300):

            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(SHEET_ID)

            print("Connected to Google Sheet ✅")

            # -------------------------
            # User Sheet
            # -------------------------
            try:
                user_sheet = spreadsheet.worksheet(USERNAME)
                print(f"Found existing sheet tab '{USERNAME}' ✅")
            except gspread.exceptions.WorksheetNotFound:
                user_sheet = spreadsheet.add_worksheet(
                    title=USERNAME,
                    rows="5000",
                    cols="10"
                )

                user_sheet.append_row([
                    "DATE",
                    "PROGRAM TITLE",
                    "LINK",
                    "DIFFICULTY",
                    "PLATFORM",
                    "TOPIC",
                    "COUNT"
                ])

                print(f"Created new sheet tab '{USERNAME}' ✅")

            # -------------------------
            # Backup Sheet
            # -------------------------
            backup_tab_name = f"{USERNAME}_Backup"

            try:
                backup_sheet = spreadsheet.worksheet(backup_tab_name)
                print(f"Backup sheet '{backup_tab_name}' found ✅")
            except gspread.exceptions.WorksheetNotFound:
                backup_sheet = spreadsheet.add_worksheet(
                    title=backup_tab_name,
                    rows="5000",
                    cols="10"
                )
                print(f"Backup sheet '{backup_tab_name}' created ✅")

            # -------------------------
            # Protect Sheet
            # -------------------------
            from sheet_protect import secure_user_tab

            secure_user_tab(
                spreadsheet,
                user_sheet,
                USER_EMAIL,
                CREDENTIALS_FILE
            )

            # -------------------------
            # Restore Command
            # -------------------------
            if len(sys.argv) > 2 and sys.argv[2] == "restore":
                print("Restore command detected 🔄")

                backup_data = backup_sheet.get_all_values()

                if backup_data:
                    user_sheet.clear()
                    user_sheet.update("A1", backup_data)
                    print("Sheet restored from backup ✅")
                else:
                    print("Backup sheet empty ❌")

                sys.exit(0)

            # -------------------------
            # Backup Command
            # -------------------------
            if len(sys.argv) > 2 and sys.argv[2] == "backup":

                print("Manual backup triggered 📦")

                data = user_sheet.get_all_values()

                backup_sheet.clear()
                backup_sheet.update("A1", data)

                print("Backup completed ✅")

                sys.exit(0)

            # -------------------------
            # Delete Backup
            # -------------------------
            if len(sys.argv) > 2 and sys.argv[2] == "delete":

                print("Delete backup command detected 🗑")

                spreadsheet.del_worksheet(backup_sheet)

                print("Backup sheet deleted successfully ✅")

                sys.exit(0)

            # -------------------------
            # Write Sheet
            # -------------------------
            write_sheet_import(
                user_sheet,
                sheet_data,
                USERNAME
            )

        print(f"📄 Written {len(sheet_data)} rows to sheet tab '{USERNAME}' ✅")

    except Exception as e:
        print(f"Sheet write error: {e}")

else:
    print("📄 No data to write to sheet.")