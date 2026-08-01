from collections import Counter
import os
import json
import time
from datetime import datetime

import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ================= CONFIG =================

SHEET_ID = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"

CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"
LC_CACHE_FILE = "lc_cache.json"

# ================= GOOGLE SHEETS =================

def get_sheet(username):
    """Get or create a per-user worksheet tab inside the main spreadsheet."""
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
    CLIENT = gspread.authorize(creds)
    spreadsheet = CLIENT.open_by_key(SHEET_ID)

    # Try to find existing tab for this user
    try:
        sheet = spreadsheet.worksheet(username)
        print(f"Found existing sheet for '{username}' ✅")
    except gspread.exceptions.WorksheetNotFound:
        # Create a new tab for this user with headers
        sheet = spreadsheet.add_worksheet(title=username, rows="5000", cols="10")
        sheet.append_row(["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"])
        print(f"Created new sheet for '{username}' ✅")

    return sheet

def regroup_sheet(sheet):
    from collections import defaultdict

    values = sheet.get_all_values()

    if len(values) <= 1:
        return

    values = values[1:]   # Skip header

    date_rows = defaultdict(list)

    current_date = ""

    for row_no, row in enumerate(values, start=2):
        if row[0].strip():
            current_date = row[0].strip()

        date_rows[current_date].append(row_no)

    try:
        sheet.spreadsheet.batch_update({
            "requests": [{
                "unmergeCells": {
                    "range": {
                        "sheetId": sheet.id
                    }
                }
            }]
        })
    except:
        pass

    requests = []

    for rows in date_rows.values():

        if len(rows) == 1:
            continue

        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": rows[0]-1,
                    "endRowIndex": rows[-1],
                    "startColumnIndex": 0,
                    "endColumnIndex": 1
                },
                "mergeType": "MERGE_ALL"
            }
        })

        requests.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": rows[0]-1,
                    "endRowIndex": rows[-1],
                    "startColumnIndex": 6,
                    "endColumnIndex": 7
                },
                "mergeType": "MERGE_ALL"
            }
        })

    if requests:
        try:
            sheet.spreadsheet.batch_update({
                "requests": requests
            })
        except Exception as e:
            print("Merge error:", e)

def rebuild_user_sheet_from_db(user_id, username, get_db):
    """
    Wipe this user's Google Sheet tab and rewrite it completely from
    PostgreSQL. PostgreSQL is always the source of truth — this is the
    "someone deleted rows in the Sheet, get them back" button.
    """
    with get_db() as db:
        subs = db.execute("""
                        SELECT
                            id,
                            solved_date,
                            problem_name,
                            problem_url,
                            submission_url,
                            difficulty,
                            platform,
                            tags
                        FROM submissions
                        WHERE user_id = ?
                        """, (user_id,)).fetchall()

    # solved_date isn't uniformly formatted across all rows — some older rows
    # were written as ISO (YYYY-MM-DD), most as DD-MM-YYYY. Sorting the raw
    # text (even with a substr trick tuned for one format) misplaces whichever
    # rows are in the other format. _parse_any_date already knows how to
    # handle both, so parse in Python and sort on the real date instead.
    from utils.helpers import _parse_any_date

    parsed = []
    for s in subs:
        dt = _parse_any_date(s["solved_date"])
        parsed.append((dt or datetime.min, s["id"], s))

    parsed.sort(key=lambda t: (t[0], t[1]))

    rows = []
    for dt, _id, s in parsed:
        title = s.get("problem_name") or "Unknown"
        url = s.get("problem_url") or ""
        submission_url = s.get("submission_url") or ""

        # Always emit DD-MM-YYYY for the sheet, regardless of how it was
        # stored in the DB. Leading apostrophe forces Sheets to keep it as
        # text instead of auto-reformatting/reinterpreting it.
        date_label = dt.strftime("%d-%m-%Y") if dt != datetime.min else (s["solved_date"] or "")
        date = "'" + date_label

        rows.append([
            date,
            f'=HYPERLINK("{url}", "{title}")' if url else title,
            url,
            s.get("difficulty") or "",
            s.get("platform") or "",
            s.get("tags") or "General",
            1,
        ])
    
    sheet = get_sheet(username)
    
    sheet.batch_clear(["A:G"])

    sheet.update(
        "A1:G1",
        [["DATE","PROGRAM TITLE","LINK","DIFFICULTY","PLATFORM","TOPIC","COUNT"]]
    )

    sheet.format(
        "A:A",
        {
            "numberFormat": {
                "type": "TEXT"
            }
        }
    )

    if rows:
        from collections import Counter

        count_map = Counter(r[0] for r in rows)

        for row in rows:
            row[6] = count_map[row[0]]
        sheet.append_rows(
    rows,
    value_input_option="USER_ENTERED"
)
        # regroup: merge DATE and COUNT cells for consecutive same-date rows
        regroup_sheet(sheet)
    print(f"🔄 Restored '{username}' sheet from DB — {len(rows)} rows")
    return len(rows)


# ================= HELPERS =================

def safe_get_json(url, headers=None, params=None, timeout=15):
    try:
        res = requests.get(url, headers=headers or {}, params=params, timeout=timeout)
        ctype = res.headers.get("Content-Type", "")
        if res.status_code != 200:
            return None, f"status={res.status_code}"
        if "json" not in ctype.lower():
            return None, f"non-json content-type={ctype}"
        return res.json(), None
    except Exception as e:
        return None, str(e)

def parse_timestamp(ts):
    if ts is None or ts == "":
        return datetime.now().strftime("%d-%m-%Y")
    try:
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if isinstance(ts, (int, float)):
            if ts > 10**12:
                ts = ts / 1000
            return datetime.fromtimestamp(int(ts)).strftime("%d-%m-%Y")
    except Exception:
        pass
    return datetime.now().strftime("%d-%m-%Y")

def load_cache(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_cache(path, rows):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ================= LEETCODE DETAILS =================

def get_lc_details(slug):
    try:
        res = requests.post(
            "https://leetcode.com/graphql",
            json={
                "query": """
                query($titleSlug: String!) {
                  question(titleSlug: $titleSlug) {
                    difficulty
                    topicTags { name }
                  }
                }
                """,
                "variables": {"titleSlug": slug},
            },
            headers={
                "Content-Type": "application/json",
                "Referer": "https://leetcode.com",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=60,
        )
        data = res.json()
        question = data.get("data", {}).get("question") or {}
        diff = question.get("difficulty", "Unknown")
        topic_tags = question.get("topicTags") or []
        topic = ", ".join(t.get("name", "") for t in topic_tags if t.get("name")) or "General"
        return diff, topic
    except Exception:
        return "Unknown", "General"

# ================= CODEFORCES =================

def fetch_cf(cf_handle):
    if not cf_handle:
        print("[CF] No handle set, skipping.")
        return []

    rows = []
    seen = set()

    url = f"https://codeforces.com/api/user.status?handle={cf_handle}&count=10000"
    data, err = safe_get_json(url, timeout=20)
    if not data or data.get("status") != "OK":
        print(f"[CF ERROR] {err or 'bad response'}")
        return []

    for sub in data.get("result", []):
        if sub.get("verdict") != "OK":
            continue

        prob = sub.get("problem", {})
        contest_id = prob.get("contestId")
        index = prob.get("index")
        if not contest_id or not index:
            continue

        pid = f"{contest_id}-{index}"
        if pid in seen:
            continue
        seen.add(pid)

        title = prob.get("name", "Unknown")
        link = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
        sub_link = f"https://codeforces.com/contest/{contest_id}/submission/{sub.get('id', '')}"

        rating = prob.get("rating")
        if rating is None:
            diff = "Medium"
        elif rating < 1200:
            diff = "Easy"
        elif rating <= 1800:
            diff = "Medium"
        else:
            diff = "Hard"

        topic = ", ".join(prob.get("tags", [])) or "General"
        date = datetime.fromtimestamp(sub["creationTimeSeconds"]).strftime("%d-%m-%Y")


        rows.append([
            date,
            f'=HYPERLINK("{url}", "{title}")',
            sub_link,
            diff,
            "Codeforces",
            topic,
            1,
        ])

    print(f"CF done ✅ ({len(rows)})")
    return rows
# ================= LEETCODE (NO COOKIE, BEST EFFORT) =================

def _normalize_lc_items(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "acSubmission", "submission", "submissions", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return val

        for key in ("data", "acSubmission", "submission", "submissions"):
            val = payload.get(key)
            if isinstance(val, dict):
                for inner in ("data", "items", "list", "rows"):
                    inner_val = val.get(inner)
                    if isinstance(inner_val, list):
                        return inner_val

    return []


# ================= LEETCODE (LATEST 20 - NO COOKIE) =================

def fetch_lc(lc_handle):
    if not lc_handle:
        print("[LC] No handle set, skipping.")
        return []

    print("[LC] Starting no-cookie fetch...")

    rows = []
    seen = set()
    cached_rows = load_cache(LC_CACHE_FILE)

    endpoints = [
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/acSubmission?limit=20",
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/submission?limit=20",
    ]

    for endpoint in endpoints:
        try:
            data, err = safe_get_json(
                endpoint,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            if not data:
                print(f"[LC] endpoint failed: {err}")
                continue

            subs = _normalize_lc_items(data)
            if not subs:
                print("[LC] endpoint returned empty list")
                continue

            temp_rows = []

            for sub in subs:
                slug = (
                    sub.get("titleSlug")
                    or sub.get("title_slug")
                    or sub.get("questionTitleSlug")
                    or ""
                )
                title = (
                    sub.get("title")
                    or sub.get("questionTitle")
                    or sub.get("titleSlug")
                    or slug
                    or "Unknown"
                )

                if not slug or slug in seen:
                    continue
                seen.add(slug)

                status_text = str(
                    sub.get("statusDisplay")
                    or sub.get("status_display")
                    or sub.get("status")
                    or sub.get("state")
                    or ""
                ).lower()
                if status_text and not any(k in status_text for k in ("accepted", "ac", "success")):
                    continue

                ts = (
                    sub.get("timestamp")
                    or sub.get("submittedAt")
                    or sub.get("date")
                    or sub.get("createdAt")
                    or int(time.time())
                )
                date = parse_timestamp(ts)

                sub_id = (
                    sub.get("id")
                    or sub.get("submissionId")
                    or sub.get("submission_id")
                    or ""
                )

                problem_link = f"https://leetcode.com/problems/{slug}/"
                sub_link = f"https://leetcode.com/submissions/detail/{sub_id}/" if sub_id else problem_link

                diff, topic = get_lc_details(slug)
                
                print("=" * 60)
                print("Slug :", slug)
                print("Difficulty :", diff)
                print("Topic :", topic)
                print("=" * 60)
                temp_rows.append([
                    date,
                    f'=HYPERLINK("{problem_link}", "{title}")',
                    sub_link,
                    diff,
                    "LeetCode",
                    topic,
                    1,
                ])

            if temp_rows:
                rows = temp_rows
                print(f"[LC] fetched {len(rows)} rows from {endpoint}")
                break

        except Exception as e:
            print(f"[LC] endpoint exception: {e}")

    if rows:
        save_cache(LC_CACHE_FILE, rows)
        print(f"LC done ✅ ({len(rows)})")
        return rows

    if cached_rows:
        print(f"[LC FINAL] Using cached data ⚠️ ({len(cached_rows)})")
        return cached_rows

    print("[LC FINAL] No submissions found ⚠️")
    return []

# ================= ATCODER =================

def fetch_ac(ac_handle):
    if not ac_handle:
        print("[AC] No handle set, skipping.")
        return []

    rows = []
    seen = set()

    data, err = safe_get_json(
        f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={ac_handle}&from_second=0",
        timeout=20,
    )
    if not data or not isinstance(data, list):
        print(f"[AC ERROR] {err or 'bad response'}")
        return []

    problem_data, _ = safe_get_json("https://kenkoooo.com/atcoder/resources/problems.json", timeout=20)
    problem_map = {}
    if isinstance(problem_data, list):
        for p in problem_data:
            pid = p.get("id")
            title = p.get("title")
            if pid and title:
                problem_map[pid] = title

    for sub in data:
        if sub.get("result") != "AC":
            continue

        pid = sub.get("problem_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)

        contest = sub.get("contest_id", "")
        sid = sub.get("id", "")
        title = problem_map.get(pid, pid)

        if "_a" in pid:
            diff, topic = "Easy", "Implementation"
        elif "_b" in pid:
            diff, topic = "Easy", "Math"
        elif "_c" in pid:
            diff, topic = "Medium", "Greedy"
        elif "_d" in pid:
            diff, topic = "Hard", "Dynamic Programming"
        else:
            diff, topic = "Unknown", "General"

        date  = datetime.fromtimestamp(sub["epoch_second"]).strftime("%d-%m-%Y")
        link = f"https://atcoder.jp/contests/{contest}/tasks/{pid}"
        sub_link = f"https://atcoder.jp/contests/{contest}/submissions/{sid}"

        rows.append([
            date,
            f'=HYPERLINK("{link}", "{title}")',
            sub_link,
            diff,
            "AtCoder",
            topic,
            1,
        ])

    print(f"AC done ✅ ({len(rows)})")
    return rows

# ================= MAIN =================

def sync_user_data(user, get_db):
    """
    Sync submissions for a single user.
    - Reads cf_handle, lc_handle, ac_handle from the user dict (saved via Settings page).
    - Writes new rows to a per-user tab in the Google Sheet (tab named = username).
    - Also inserts into the PostgreSQL submissions table with correct user_id.
    """
    cf_handle = (user.get("cf_handle") or "").strip()
    lc_handle = (user.get("lc_handle") or "").strip()
    ac_handle = (user.get("ac_handle") or "").strip()
    username  = (user.get("username") or f"user_{user['id']}").strip()

    print(f"\n🔄 Syncing for user: {username}")
    print(f"   CF={cf_handle or '(none)'}  LC={lc_handle or '(none)'}  AC={ac_handle or '(none)'}")

    all_data = []

    print("Fetching CF...")
    all_data.extend(fetch_cf(cf_handle))

    print("Fetching LC...")
    all_data.extend(fetch_lc(lc_handle))

    print("Fetching AC...")
    all_data.extend(fetch_ac(ac_handle))

    if not all_data:
        return {"success": False, "message": "No data fetched", "new_count": 0}

    conn = get_db()
    cursor = conn.cursor()

    user_id = user["id"]
    new_count = 0
    new_rows = []

    for row in all_data:
        title = row[1].split('", "')[-1].rstrip('")') if 'HYPERLINK(' in row[1] and '", "' in row[1] else row[1]
        platform = row[4].replace("🟦", "").replace("🟨", "").replace("🟥", "").strip()
        problem_url = row[1].split('"')[1] if '"' in row[1] else row[2]

        if "leetcode.com/problems/" in problem_url:
            problem_id = problem_url.split("/problems/")[-1].split("/")[0]
        elif "codeforces.com/problemset/problem" in problem_url:
            parts = problem_url.split("/")
            problem_id = parts[-2] + "-" + parts[-1]
        elif "atcoder.jp/contests" in problem_url:
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
                    platform,
                    difficulty,
                    tags,
                    solved_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, platform, problem_id) DO NOTHING
                """, (
                    user_id,
                    title,
                    problem_id,
                    problem_url,
                    platform,
                    row[3],      # difficulty
                    row[5],      # <-- TAGS
                    row[0]       # solved_date
                ))

        if cursor.rowcount > 0:
            new_count += 1
            new_rows.append(row)

    conn.commit()
    conn.close()

    # Write new rows to this user's own tab in the Google Sheet
    try:
        sheet = get_sheet(username)
        if new_rows:
            from collections import Counter

            count_map = Counter(r[0] for r in new_rows)

            for row in new_rows:
                row[6] = count_map[row[0]]

            rebuild_user_sheet_from_db(
    user_id,
    username,
    get_db
)
            print(f"📄 Added {len(new_rows)} rows to tab '{username}'")
        else:
            print(f"📄 No new rows for '{username}'")
    except Exception as e:
        print("Sheet error:", e)

    return {
        "success": True,
        "message": f"{new_count} new problems added",
        "new_count": new_count
    }