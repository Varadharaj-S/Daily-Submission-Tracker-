import requests
import pandas as pd
import gspread
import time
import sys
import json
import os
import re
from datetime import datetime
from google.oauth2.service_account import Credentials

# ===============================
# CONFIG
# ===============================

SHEET_ID = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"

# NOTE: this whole script (automation_updated.py) is legacy/unused — the live
# app uses bot_sheet_sync.py per-user instead. Left here only for reference,
# with the old hardcoded personal handles and config.json cookie removed so
# it can't accidentally leak/reuse one person's LeetCode session.
CODEFORCES_USER = ""
LEETCODE_USER   = ""
ATCODER_USER    = ""

CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"

# ===============================
# LeetCode session must come from the DB per-user (see bot_sheet_sync.py).
# config.json fallback removed intentionally — do not re-add it.
# ===============================

LEETCODE_SESSION = ""
LEETCODE_CSRF    = ""

# ===============================
# LEETCODE DETAILS (difficulty + tags)
# ===============================

def get_leetcode_details(slug, session_obj=None):
    url = "https://leetcode.com/graphql"
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
# GOOGLE SHEETS AUTH
# ===============================

# import os
import json
# from google.oauth2.service_account import Credentials

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
spreadsheet = client.open_by_key(SHEET_ID)
main_sheet  = spreadsheet.sheet1

print("Connected to Google Sheet ✅")

# ===============================
# BACKUP SHEET CHECK
# ===============================

try:
    backup_sheet = spreadsheet.worksheet("Backup")
    print("Backup sheet found ✅")
except:
    print("Backup sheet not found, creating one...")
    backup_sheet = spreadsheet.add_worksheet(title="Backup", rows="5000", cols="10")
    print("Backup sheet created ✅")

# ===============================
# RESTORE COMMAND
# ===============================

if len(sys.argv) > 1 and sys.argv[1] == "restore":
    print("Restore command detected 🔄")
    backup_data = backup_sheet.get_all_values()
    if backup_data:
        main_sheet.clear()
        main_sheet.update(values=backup_data, range_name="A1")
        print("Sheet restored from backup ✅")
    else:
        print("Backup sheet empty ❌")
    exit()

# ===============================
# BACKUP COMMAND
# ===============================

if len(sys.argv) > 1 and sys.argv[1] == "backup":
    print("Manual backup triggered 📦")
    data = main_sheet.get_all_values()
    backup_sheet.clear()
    backup_sheet.update(values=data, range_name="A1")
    print("Backup completed ✅")
    exit()

# ===============================
# DELETE BACKUP COMMAND
# ===============================

if len(sys.argv) > 1 and sys.argv[1] == "delete":
    print("Delete backup command detected 🗑")
    try:
        spreadsheet.del_worksheet(backup_sheet)
        print("Backup sheet deleted successfully ✅")
    except:
        print("Backup sheet not found ❌")
    exit()

all_data = []

# ===============================
# CODEFORCES
# ===============================

print("Fetching Codeforces...")

cf_url  = f"https://codeforces.com/api/user.status?handle={CODEFORCES_USER}&count=10000"
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
        title_cell      = f'=HYPERLINK("{problem_link}", "{title}")'
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
        date  = datetime.fromtimestamp(sub["creationTimeSeconds"]).strftime("%d-%m-%Y")

        all_data.append([date, title_cell, submission_link, difficulty, "🟦 Codeforces", topic, 1])

print("Codeforces collected ✅")

# ===============================
# LEETCODE  — session cookie based, full history
# ===============================

print("Fetching LeetCode...")

LC_GQL = "https://leetcode.com/graphql"

# Build an authenticated requests.Session
lc_sess = requests.Session()
lc_sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://leetcode.com/",
})

# Set session cookie
if LEETCODE_SESSION:
    lc_sess.cookies.set("LEETCODE_SESSION", LEETCODE_SESSION, domain=".leetcode.com")

# Get / refresh csrf token automatically from homepage
try:
    lc_sess.get("https://leetcode.com/", timeout=60)
    auto_csrf = lc_sess.cookies.get("csrftoken", "")
    if auto_csrf:
        LEETCODE_CSRF = auto_csrf          # prefer fresh token
except Exception as e:
    print(f"  Could not refresh csrf: {e}")

if LEETCODE_CSRF:
    lc_sess.headers["X-CSRFToken"] = LEETCODE_CSRF
    lc_sess.cookies.set("csrftoken", LEETCODE_CSRF, domain=".leetcode.com")

# ── verify login ──────────────────────────────────────────────────────────────
lc_logged_in = False
try:
    r = lc_sess.post(LC_GQL,
                     json={"query": "query{userStatus{username isSignedIn}}"},
                     timeout=60)
    if (r.json().get("data") or {}).get("userStatus", {}).get("isSignedIn"):
        lc_logged_in = True
        print("  LeetCode session verified ✅")
    else:
        print("  LeetCode session NOT verified ⚠️  — cookie may be expired")
except Exception as e:
    print(f"  LeetCode verify error: {e}")

# ── fetch ALL accepted submissions via REST /api/submissions/ ─────────────────
lc_seen   = set()
lc_offset = 0

lc_sess.headers.update({
    "Referer":          f"https://leetcode.com/{LEETCODE_USER}/",
    "X-Requested-With": "XMLHttpRequest",
    "Cookie":           f"LEETCODE_SESSION={LEETCODE_SESSION}; csrftoken={LEETCODE_CSRF}",
})

while True:
    try:
        r = lc_sess.get(
            "https://leetcode.com/api/submissions/",
            params={"offset": lc_offset, "limit": 20},
            timeout=20
        )
    except Exception:
        print("  Connection error, retrying...")
        time.sleep(5)
        continue

    if r.status_code == 403:
        print(f"  403 at offset {lc_offset} — cookie expired. Update config.json ❌")
        break
    if r.status_code != 200:
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

        date  = datetime.fromtimestamp(int(sub["timestamp"])).strftime("%d-%m-%Y")
        title = sub.get("title", slug)

        difficulty, topic = get_leetcode_details(slug, session_obj=lc_sess)
        time.sleep(0.4)

        problem_link    = f"https://leetcode.com/problems/{slug}/"
        submission_link = f"https://leetcode.com/submissions/detail/{sub['id']}/"
        title_cell      = f'=HYPERLINK("{problem_link}", "{title}")'

        all_data.append([date, title_cell, submission_link, difficulty, "🟨 LeetCode", topic, 1])

    if not data.get("has_next", False):
        break

    lc_offset += 20
    time.sleep(0.3)

print(f"LeetCode collected ✅  ({len(lc_seen)} unique problems)")

# ===============================
# ATCODER
# ===============================

print("Fetching AtCoder...")

url          = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={ATCODER_USER}&from_second=0"
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

    contest     = sub["contest_id"]
    submission_id = sub["id"]
    date        = datetime.fromtimestamp(sub["epoch_second"]).strftime("%d-%m-%Y")
    title       = problem_map.get(problem, problem)

    problem_link    = f"https://atcoder.jp/contests/{contest}/tasks/{problem}"
    submission_link = f"https://atcoder.jp/contests/{contest}/submissions/{submission_id}"
    title_cell      = f'=HYPERLINK("{problem_link}", "{title}")'

    if "_a" in problem:
        difficulty, topic = "Easy",   "Implementation"
    elif "_b" in problem:
        difficulty, topic = "Easy",   "Math"
    elif "_c" in problem:
        difficulty, topic = "Medium", "Greedy"
    elif "_d" in problem:
        difficulty, topic = "Hard",   "Dynamic Programming"
    else:
        difficulty, topic = "Unknown","General"

    all_data.append([date, title_cell, submission_link, difficulty, "🟥 AtCoder", topic, 1])

print("AtCoder collected ✅")

# ===============================
# DATAFRAME
# ===============================

df = pd.DataFrame(all_data, columns=[
    "DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"
])

df = df.drop_duplicates(subset=["DATE", "PROGRAM TITLE", "PLATFORM"])
df["DATE"] = pd.to_datetime(df["DATE"], dayfirst=True)
df = df.sort_values(by="DATE")
df["COUNT"] = df.groupby("DATE")["PROGRAM TITLE"].transform("count")
df["DATE"]  = df["DATE"].dt.strftime("%d-%m-%Y")

# ===============================
# UPDATE SHEET
# ===============================

main_sheet.clear()

header = [["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"]]
main_sheet.update(values=header, range_name="A1")
main_sheet.update(values=df.values.tolist(), range_name="A2", value_input_option="USER_ENTERED")

print("Sheet rebuilt perfectly 🚀")
