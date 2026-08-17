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
import concurrent.futures
import psycopg2.extras
from filelock import FileLock
from database.db import get_db
from datetime import datetime
from google.oauth2.service_account import Credentials
from utils.helpers import solved_on_iso

# ===============================
# CONFIG
# ===============================

SHEET_ID         = None  # PHASE 2: resolved per-user below from cohort_year via services/year_sheet_service.py (no more shared spreadsheet)
CREDENTIALS_FILE ="valiant-splicer-489013-q2-40d3ac23a2d8.json"

# PERF: shared LeetCode problem-details cache (difficulty/tags by slug).
# A problem's difficulty/tags basically never change and are NOT user-specific
# — every user who has ever solved "two-sum" gets the identical GraphQL
# response. Previously EVERY accepted submission (for EVERY import, for
# EVERY user, every single time /import_lc was re-run) triggered its own
# GraphQL POST + a 0.35s sleep. This cache makes that a one-time cost per
# problem, globally, instead of a per-submission/per-user/per-run cost.
# Separate file from lc_cache.json (which normal_sync.py/lc_service.py use
# for a different purpose — cached sheet ROWS, not problem metadata) to
# avoid any schema/format collision with that existing, unrelated cache.
LC_DETAILS_CACHE_FILE = "lc_problem_details_cache.json"
LC_DETAILS_CACHE_LOCK = os.path.join(os.environ.get("TMPDIR", "/tmp"), "lc_details_cache.lock")


def _load_lc_details_cache():
    try:
        with FileLock(LC_DETAILS_CACHE_LOCK, timeout=10):
            if not os.path.exists(LC_DETAILS_CACHE_FILE):
                return {}
            with open(LC_DETAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ Could not load LC details cache (continuing without it): {e}")
        return {}


def _save_lc_details_cache(new_entries):
    """Merges new_entries into the on-disk cache under a file lock (read-modify-write,
    since multiple import subprocesses can run concurrently for different users)."""
    if not new_entries:
        return
    try:
        with FileLock(LC_DETAILS_CACHE_LOCK, timeout=10):
            existing = {}
            if os.path.exists(LC_DETAILS_CACHE_FILE):
                try:
                    with open(LC_DETAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        existing = loaded
                except Exception:
                    existing = {}
            existing.update(new_entries)
            with open(LC_DETAILS_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Could not save LC details cache (non-fatal): {e}")

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
COHORT_YEAR = (user_row.get("cohort_year") or "").strip() or None

# LeetCode session/csrf from user's saved settings
LEETCODE_SESSION = (user_row.get("lc_session_cookie") or "").strip()
LEETCODE_CSRF    = (user_row.get("lc_csrf_token")     or "").strip()

print(f"User: {USERNAME}  CF={CF_USER or '(none)'}  LC={LC_USER or '(none)'}  AC={AC_USER or '(none)'}")
print("=" * 50)
print("IMPORT STARTED")
print(f"USER ID: {user_id}")
print(f"USERNAME: {USERNAME}")
print("=" * 50)

_T_START = time.time()
_T_FETCH_START = None
_T_FETCH_END = None
_T_DB_END = None
_T_SHEET_END = None

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
# CODEFORCES  (unchanged logic — wrapped in a function so it can run
# concurrently with LeetCode/AtCoder instead of blocking them)
# ===============================

def fetch_codeforces():
    """Returns (rows, count, error). Never raises — errors are captured and
    reported so one platform's failure never stops the others."""
    rows = []
    if not CF_USER:
        return rows, 0, None
    try:
        print(f"[CODEFORCES] STARTED")
        cf_url   = f"https://codeforces.com/api/user.status?handle={CF_USER}&count=10000"
        response = requests.get(cf_url, timeout=30).json()

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
                epoch = int(sub["creationTimeSeconds"])
                date = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")

                rows.append([
                    date, title_cell, submission_link, difficulty,
                    "Codeforces", topic, 1, epoch
                ])
        else:
            return rows, len(rows), f"CF API status={response.get('status')}"

        print(f"[CODEFORCES] COMPLETED rows={len(rows)}")
        print(f"Rows fetched: {len(rows)}")
        return rows, len(rows), None
    except Exception as e:
        print(f"[CODEFORCES] FAILED error={e}")
        return rows, len(rows), str(e)

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

class _LcHardFailure(Exception):
    """Raised when LC cookie/login verification fails even after reload —
    mirrors the OLD script's sys.exit(1) at this point. We can't literally
    exit the whole process from inside a worker thread (that would only kill
    the thread, silently, which is worse — it would swallow CF/AtCoder
    results too without saying why). Instead this is raised, propagated back
    to the main thread via the future, and the main thread performs the same
    "abort, write nothing" behavior the original script had — see the
    concurrent-fetch section below. Net effect on the user is unchanged;
    only the exact moment of the abort shifts slightly later (after the
    concurrent CF/AC fetches finish instead of mid-fetch)."""
    pass


def fetch_leetcode():
    """Returns (rows, count, error, cache_hits, cache_misses).
    Raises _LcHardFailure to replicate the original sys.exit(1) behavior on
    unrecoverable cookie failure (see class docstring)."""
    rows = []
    if not LC_USER:
        return rows, 0, None, 0, 0

    print(f"[LEETCODE] STARTED")
    print(f"Fetching LeetCode for '{LC_USER}'...")

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
            raise _LcHardFailure("LeetCode login failed (cookie expired, no reconnect available)")

        # 🔥 reload new cookie
        session_cookie, csrf = fetch_leetcode_cookie_from_db(user_id)
        lc_sess = build_leetcode_session(session_cookie, csrf)

        lc_logged_in, _ = verify_leetcode_login(lc_sess)

        if not lc_logged_in:
            print("❌ Even after login, verification failed")
            raise _LcHardFailure("LeetCode login verification failed after reload")

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
    lc_error = None
    if lc_logged_in:
        print("  LeetCode session verified ✅")
    else:
        lc_error = "session not verified"
        print("  LeetCode session NOT verified ⚠️  — cookie may be expired")
        if "error" in login_data:
            print(f"  LeetCode verify error: {login_data['error']}")
            lc_error = login_data["error"]
        else:
            print(f"  LOGIN CHECK: {login_data}")

    # PERF: shared slug -> [difficulty, topic] cache. A problem's metadata
    # is identical for every user, so once ANY import has fetched it, no
    # future import (for any user) needs to hit LC's GraphQL API for that
    # slug again. This is the single biggest LC bottleneck fix: previously
    # every accepted submission cost one GraphQL POST + a 0.35s sleep, every
    # single time /import_lc ran, for every user, even for problems already
    # looked up thousands of times before.
    details_cache = _load_lc_details_cache()
    new_cache_entries = {}
    cache_hits = 0
    cache_misses = 0

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
            lc_error = str(e)
            break

        if r.status_code == 401:
            lc_error = "401 unauthorized"
            print("  LeetCode API returned 401, stopping.")
            break
        if r.status_code == 403:
            lc_error = f"403 at offset {lc_offset} — cookie expired"
            print(f"  403 at offset {lc_offset} — cookie expired. Update settings ❌")
            break
        if r.status_code != 200:
            lc_error = f"HTTP {r.status_code}"
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

            # PERF: cache hit → skip the GraphQL call AND its 0.35s sleep
            # entirely. Cache miss → fetch exactly as before, then remember
            # it (same difficulty/topic values either way — pure reuse of
            # already-available data, no behavior change to the output row).
            cached = details_cache.get(slug)
            if cached:
                difficulty, topic = cached[0], cached[1]
                cache_hits += 1
            else:
                difficulty, topic = get_leetcode_details(slug, session_obj=lc_sess)
                time.sleep(0.35)
                new_cache_entries[slug] = [difficulty, topic]
                cache_misses += 1

            problem_link    = f"https://leetcode.com/problems/{slug}/"
            submission_link = f"https://leetcode.com/submissions/detail/{sub['id']}/"
            title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

            rows.append([date, title_cell, submission_link, difficulty, "LeetCode", topic, 1, int(ts)])

        if not data.get("has_next", False):
            break

        lc_offset += 20
        time.sleep(0.25)

    # Persist newly-learned slugs so the NEXT import (this user or any other)
    # gets the cache-hit benefit. Failure here is non-fatal — worst case we
    # just re-fetch those slugs next time, same as before this change.
    _save_lc_details_cache(new_cache_entries)

    print(f"[LEETCODE] COMPLETED rows={len(lc_seen)} cache_hits={cache_hits} cache_misses={cache_misses}")
    print(f"Rows fetched: {len(lc_seen)}")
    return rows, len(lc_seen), lc_error, cache_hits, cache_misses

# ===============================
# ATCODER  (unchanged logic — wrapped in a function so it can run
# concurrently with Codeforces/LeetCode instead of blocking them)
# ===============================

def fetch_atcoder():
    """Returns (rows, count, error). Never raises."""
    rows = []
    if not AC_USER:
        return rows, 0, None
    try:
        print(f"[ATCODER] STARTED")
        url          = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={AC_USER}&from_second=0"
        submissions  = requests.get(url, timeout=30).json()
        problem_data = requests.get("https://kenkoooo.com/atcoder/resources/problems.json", timeout=30).json()
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

            rows.append([date, title_cell, submission_link, difficulty, "AtCoder", topic, 1, sub["epoch_second"]])

        print(f"[ATCODER] COMPLETED rows={len(rows)}")
        print(f"Rows fetched: {len(rows)}")
        return rows, len(rows), None
    except Exception as e:
        print(f"[ATCODER] FAILED error={e}")
        return rows, len(rows), str(e)


# ===============================
# RUN CF / LC / AC CONCURRENTLY
# ===============================
# Bounded concurrency (max 3 — one per platform, no unbounded thread growth).
# Independent platforms only — none of these three depend on each other's
# output. Each returns its own real result/error; one platform failing does
# not stop or corrupt the others' data.

_T_FETCH_START = time.time()

_cf_rows, _cf_count, _cf_err = [], 0, None
_ac_rows, _ac_count, _ac_err = [], 0, None
_lc_rows, _lc_count, _lc_err = [], 0, None
_lc_cache_hits, _lc_cache_misses = 0, 0
_lc_hard_failure = None

if not CF_USER:
    print("[CODEFORCES] No handle set, skipping.")
if not AC_USER:
    print("[ATCODER] No handle set, skipping.")
if not LC_USER:
    print("[LEETCODE] No handle set, skipping.")

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _pool:
    _futures = {}
    if CF_USER:
        _futures[_pool.submit(fetch_codeforces)] = "cf"
    if AC_USER:
        _futures[_pool.submit(fetch_atcoder)] = "ac"
    if LC_USER:
        _futures[_pool.submit(fetch_leetcode)] = "lc"

    for _fut in concurrent.futures.as_completed(_futures):
        _which = _futures[_fut]
        try:
            if _which == "cf":
                _cf_rows, _cf_count, _cf_err = _fut.result()
            elif _which == "ac":
                _ac_rows, _ac_count, _ac_err = _fut.result()
            elif _which == "lc":
                _lc_rows, _lc_count, _lc_err, _lc_cache_hits, _lc_cache_misses = _fut.result()
        except _LcHardFailure as e:
            # Same end state as the original script's sys.exit(1) here:
            # abort before anything is written to DB/Sheet. See
            # _LcHardFailure's docstring for why this is deferred to here
            # instead of an immediate process exit.
            _lc_hard_failure = str(e)

if _lc_hard_failure:
    print(f"[IMPORT] FAILED stage=LEETCODE_LOGIN error={_lc_hard_failure}")
    print("=" * 50)
    print("IMPORT FAILED")
    print(f"USER ID: {user_id}")
    print(f"USERNAME: {USERNAME}")
    print("FAILED STAGE: LEETCODE_LOGIN")
    print("STATUS: FAILED")
    print("=" * 50)
    sys.exit(1)

_T_FETCH_END = time.time()
print(f"[IMPORT] FETCH COMPLETE elapsed={_T_FETCH_END - _T_FETCH_START:.2f}s "
      f"cf_rows={_cf_count} lc_rows={_lc_count} ac_rows={_ac_count} "
      f"lc_cache_hits={_lc_cache_hits} lc_cache_misses={_lc_cache_misses}")
if _cf_err:
    print(f"[CODEFORCES] error={_cf_err}")
if _ac_err:
    print(f"[ATCODER] error={_ac_err}")
if _lc_err:
    print(f"[LEETCODE] error={_lc_err}")

print("[IMPORT] COMBINING RESULTS")
all_data.extend(_cf_rows)
all_data.extend(_lc_rows)
all_data.extend(_ac_rows)

# ===============================
# DATAFRAME — sort & dedup
# ===============================

_raw_row_count = len(all_data)

if all_data:
    df = pd.DataFrame(all_data, columns=[
        "DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT", "EPOCH"
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
    sheet_data = sheet_data.drop(columns=["EPOCH"])  # sheet only ever had 7 columns — keep it that way

    print(f"[IMPORT] DEDUP COMPLETE raw_rows={_raw_row_count} unique_rows={len(df)}")
    print("[IMPORT] DEDUPLICATION COMPLETED")
    print(f"Raw rows: {_raw_row_count}")
    print(f"Unique rows: {len(df)}")

# ===============================
# SAVE TO POSTGRESQL
# (was: one INSERT + implicit per-row round trip in a loop, one commit at
#  the end. Same ON CONFLICT DO NOTHING semantics, same columns, same
#  values — now sent as a single batched statement via execute_values
#  instead of N separate round trips to the DB. RETURNING + counting the
#  returned rows keeps new_count exactly as accurate as the old
#  cursor.rowcount-per-row check, just computed from one round trip.)
# ===============================

_T_DB_START = time.time()
print("[DATABASE] BATCH WRITE STARTED")

conn   = get_db()
cursor = conn.cursor()
new_count = 0

_insert_params = []
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

    # row[7] is EPOCH (raw epoch seconds) — pandas may hand it back as a
    # numpy float, so guard against NaN/None before converting.
    epoch = row[7] if len(row) > 7 else None
    submitted_at = datetime.fromtimestamp(int(epoch)) if epoch is not None and not pd.isna(epoch) else None

    _insert_params.append((
        user_id,
        title,
        problem_id,
        problem_url,
        submission_url,
        row[4],   # platform
        row[3],   # difficulty
        row[5],   # tags
        row[0],   # solved_date
        submitted_at,
        solved_on_iso(row[0]),   # real DATE mirror — see utils/helpers.py
    ))

if _insert_params:
    _insert_sql = """
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
    solved_date,
    submitted_at,
    solved_on
)
VALUES %s
ON CONFLICT (user_id, platform, problem_id)
DO NOTHING
RETURNING id
"""
    try:
        _returned = psycopg2.extras.execute_values(
            cursor._cur, _insert_sql, _insert_params, page_size=500, fetch=True
        )
        new_count = len(_returned) if _returned is not None else 0
    except AttributeError:
        # Fallback: if the Cursor wrapper's internal attribute name ever
        # changes, don't silently write nothing — fall back to the
        # original, known-correct per-row path rather than fail the import.
        print("⚠️ execute_values fast path unavailable, falling back to per-row insert")
        new_count = 0
        for params in _insert_params:
            cursor.execute("""
INSERT INTO submissions
(
    user_id, problem_name, problem_id, problem_url, submission_url,
    platform, difficulty, tags, solved_date, submitted_at, solved_on
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (user_id, platform, problem_id)
DO NOTHING
""", params)
            if cursor.rowcount > 0:
                new_count += 1

conn.commit()
conn.close()

_T_DB_END = time.time()

print(f"[IMPORT] DB BATCH WRITE rows={len(_insert_params)} new={new_count} elapsed={_T_DB_END - _T_DB_START:.2f}s")
print(f"[IMPORT] Database updated ({new_count} new rows inserted)")
print("[DATABASE] BATCH WRITE COMPLETED")
print(f"Rows written: {new_count}")


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

            from services.year_sheet_service import get_sheet_id_for_year
            if not COHORT_YEAR:
                raise RuntimeError(f"'{USERNAME}' has no year/cohort assigned yet — data was saved to the "
                                    "database, but skipping the Google Sheet write until a mentor assigns a year.")
            SHEET_ID = get_sheet_id_for_year(COHORT_YEAR)
            if not SHEET_ID:
                raise RuntimeError(f"No Google Sheet configured for year '{COHORT_YEAR}' yet — ask a mentor to set one up.")

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
            _T_SHEET_START = time.time()
            print("[GOOGLE SHEET] BATCH WRITE STARTED")
            write_sheet_import(
                user_sheet,
                sheet_data,
                USERNAME
            )
            _T_SHEET_END = time.time()
            print(f"[IMPORT] SHEET BATCH WRITE rows={len(sheet_data)} elapsed={_T_SHEET_END - _T_SHEET_START:.2f}s")
            print("[GOOGLE SHEET] BATCH WRITE COMPLETED")
            print(f"Rows written: {len(sheet_data)}")

        print(f"[IMPORT] Year-specific sheet updated ({len(sheet_data)} rows written to tab '{USERNAME}')")
        print("[IMPORT] Import completed successfully")
        print(f"[IMPORT] COMPLETED total_elapsed={time.time() - _T_START:.2f}s "
              f"cf={_cf_count} lc={_lc_count} ac={_ac_count} "
              f"unique_rows={len(df) if all_data else 0} db_new={new_count} sheet_rows={len(sheet_data)}")
        print("=" * 50)
        print("IMPORT COMPLETED")
        print(f"USER ID: {user_id}")
        print(f"USERNAME: {USERNAME}")
        print("STATUS: SUCCESS")
        print("=" * 50)

    except Exception as e:
        import traceback
        _safe_err = str(e).replace(str(os.environ.get("DATABASE_URL", "")), "[DATABASE_URL]") \
                          .replace(str(os.environ.get("GOOGLE_SERVICE_JSON", ""))[:20], "[GOOGLE_SERVICE_JSON]")
        print(f"[IMPORT] FAILED stage=SHEET_WRITE error={_safe_err}")
        print(f"[IMPORT] Traceback: {traceback.format_exc().splitlines()[-1]}")
        print("=" * 50)
        print("IMPORT FAILED")
        print(f"USER ID: {user_id}")
        print(f"USERNAME: {USERNAME}")
        print("FAILED STAGE: SHEET_WRITE")
        print("STATUS: FAILED")
        print("=" * 50)

else:
    print("📄 No data to write to sheet.")
    print(f"[IMPORT] COMPLETED total_elapsed={time.time() - _T_START:.2f}s "
          f"cf={_cf_count} lc={_lc_count} ac={_ac_count} unique_rows=0 db_new={new_count} sheet_rows=0")
    print("=" * 50)
    print("IMPORT COMPLETED")
    print(f"USER ID: {user_id}")
    print(f"USERNAME: {USERNAME}")
    print("STATUS: SUCCESS")
    print("=" * 50)