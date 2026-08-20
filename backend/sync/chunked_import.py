"""
sync/chunked_import.py

Shared building blocks for the three independent, single-request import
actions (routes/sync.py: /import_codeforces, /import_atcoder,
/import_leetcode). Each request does ONE thing and returns the real final
result — no polling, no background jobs.

This module does not invent new fetch/dedupe/DB/Sheet logic. The
Codeforces/AtCoder/LeetCode fetch functions, the pandas dedup step, the
batched Postgres insert, and the Sheet write are the SAME logic
bot_sheet_sync.py already used (that script is left untouched — it's
still used by workers/report_worker.py) — copied here as importable
functions instead of top-of-module script code, which is what let the
combined /import_lc exceed Vercel's execution limit in the first place.

Sheet strategy: instead of each request writing only "the rows it just
fetched" (which would either require holding cross-request state or would
wipe out other platforms' rows on every Sheet rebuild), every request
rebuilds the user's Sheet tab from the CURRENT FULL Postgres state for
that user, right after its own DB write. Postgres is already the source
of truth (ON CONFLICT DO NOTHING keeps it de-duplicated across runs), so
this keeps the Sheet always fully consistent — including whichever other
platforms were imported in earlier requests — while still being exactly
one batched clear+write Sheet call per request (see rebuild_user_sheet).
"""

import os
import re
import json
import time
import concurrent.futures
from collections import defaultdict
from datetime import datetime

import requests
import pandas as pd
import gspread
import psycopg2.extras
from filelock import FileLock

from database.db import get_db
from services.year_sheet_service import open_year_spreadsheet
from sheet_protect import secure_user_tab
from utils.helpers import solved_on_iso

CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"

# Same cache file bot_sheet_sync.py already uses — a problem's
# difficulty/tags are identical for every user, so sharing the file means
# the LeetCode chunks benefit from cache entries any other import (old or
# new endpoint) has already warmed, and vice versa.
LC_DETAILS_CACHE_FILE = "lc_problem_details_cache.json"
LC_DETAILS_CACHE_LOCK = os.path.join(os.environ.get("TMPDIR", "/tmp"), "lc_details_cache.lock")

SHEET_LOCK_PATH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "sheet_sync.lock")

# ── Conservative LeetCode chunk budget ───────────────────────────────────
# Deliberately NOT tuned to sit just under the deployed Vercel limit — see
# routes/sync.py module docstring. Both bounds apply; whichever is hit
# first ends the chunk. Tune via env if the deployed function limit
# changes; defaults assume a limit comfortably above these numbers.
LEETCODE_CHUNK_TIME_BUDGET_SECONDS = float(os.environ.get("LEETCODE_CHUNK_TIME_BUDGET_SECONDS", "60"))
LEETCODE_CHUNK_MAX_PAGES = int(os.environ.get("LEETCODE_CHUNK_MAX_PAGES", "40"))  # 40 * 20 = 800 submissions/request, hard ceiling
LEETCODE_PAGE_LIMIT = 20  # unchanged from bot_sheet_sync.py — LeetCode's own submissions API page size


# ===============================================================
# LeetCode problem-details cache (difficulty/tags by slug) — copied as-is
# ===============================================================

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
        "variables": {"titleSlug": slug},
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
        data = res.json()
        question = data["data"]["question"]
        difficulty = question["difficulty"]
        tags = question["topicTags"]
        topic = ", ".join(tag["name"] for tag in tags) or "General"
        return difficulty, topic
    except Exception:
        return "Unknown", "General"


# ===============================================================
# LeetCode session helpers — copied as-is from bot_sheet_sync.py
# ===============================================================

def build_leetcode_session(session_cookie="", csrf_token=""):
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://leetcode.com",
        "Referer": "https://leetcode.com/",
        "X-Requested-With": "XMLHttpRequest",
    })

    if session_cookie:
        if "LEETCODE_SESSION=" in session_cookie:
            m = re.search(r"LEETCODE_SESSION=([^;]+)", session_cookie)
            if m:
                session_cookie = m.group(1)
        session_cookie = session_cookie.strip()
        sess.cookies.set("LEETCODE_SESSION", session_cookie, domain=".leetcode.com", path="/")

    if csrf_token:
        csrf_token = csrf_token.strip()
        sess.cookies.set("csrftoken", csrf_token, domain=".leetcode.com", path="/")
        sess.headers["X-CSRFToken"] = csrf_token

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


# ===============================================================
# CODEFORCES — unchanged logic from bot_sheet_sync.py's fetch_codeforces()
# ===============================================================

def fetch_codeforces(cf_user):
    """Returns (rows, count, error). Never raises."""
    rows = []
    if not cf_user:
        return rows, 0, None
    try:
        print("[CODEFORCES] FETCH STARTED")
        cf_url = f"https://codeforces.com/api/user.status?handle={cf_user}&count=10000"
        response = requests.get(cf_url, timeout=30).json()

        if response["status"] == "OK":
            for sub in response["result"]:
                if sub.get("verdict") != "OK":
                    continue
                problem = sub["problem"]
                contest_id = problem.get("contestId")
                index = problem.get("index")
                if not contest_id or not index:
                    continue

                problem_link = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
                submission_link = f"https://codeforces.com/contest/{contest_id}/submission/{sub['id']}"
                title = problem.get("name", "Unknown")
                title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

                rating = problem.get("rating")
                if rating is None:
                    difficulty = "Medium"
                elif rating < 1200:
                    difficulty = "Easy"
                elif rating <= 1800:
                    difficulty = "Medium"
                else:
                    difficulty = "Hard"

                tags = problem.get("tags", [])
                topic = ", ".join(tags) if tags else "General"
                epoch = int(sub["creationTimeSeconds"])
                date = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")

                rows.append([date, title_cell, submission_link, difficulty, "Codeforces", topic, epoch])
        else:
            return rows, len(rows), f"CF API status={response.get('status')}"

        print(f"[CODEFORCES] FETCH COMPLETED")
        print(f"Rows fetched: {len(rows)}")
        return rows, len(rows), None
    except Exception as e:
        print(f"[CODEFORCES] FAILED error={e}")
        return rows, len(rows), str(e)


# ===============================================================
# ATCODER — unchanged logic from bot_sheet_sync.py's fetch_atcoder()
# ===============================================================

def fetch_atcoder(ac_user):
    """Returns (rows, count, error). Never raises."""
    rows = []
    if not ac_user:
        return rows, 0, None
    try:
        print("[ATCODER] FETCH STARTED")
        url = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={ac_user}&from_second=0"
        submissions = requests.get(url, timeout=30).json()
        problem_data = requests.get("https://kenkoooo.com/atcoder/resources/problems.json", timeout=30).json()
        problem_map = {p["id"]: p["title"] for p in problem_data}

        seen = set()
        for sub in submissions:
            if sub["result"] != "AC":
                continue
            problem = sub["problem_id"]
            if problem in seen:
                continue
            seen.add(problem)

            contest = sub["contest_id"]
            submission_id = sub["id"]
            date = datetime.fromtimestamp(sub["epoch_second"]).strftime("%Y-%m-%d")
            title = problem_map.get(problem, problem)

            problem_link = f"https://atcoder.jp/contests/{contest}/tasks/{problem}"
            submission_link = f"https://atcoder.jp/contests/{contest}/submissions/{submission_id}"
            title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

            if "_a" in problem:
                difficulty, topic = "Easy", "Implementation"
            elif "_b" in problem:
                difficulty, topic = "Easy", "Math"
            elif "_c" in problem:
                difficulty, topic = "Medium", "Greedy"
            elif "_d" in problem:
                difficulty, topic = "Hard", "Dynamic Programming"
            else:
                difficulty, topic = "Unknown", "General"

            rows.append([date, title_cell, submission_link, difficulty, "AtCoder", topic, sub["epoch_second"]])

        print(f"[ATCODER] FETCH COMPLETED")
        print(f"Rows fetched: {len(rows)}")
        return rows, len(rows), None
    except Exception as e:
        print(f"[ATCODER] FAILED error={e}")
        return rows, len(rows), str(e)


# ===============================================================
# LEETCODE — chunked. Same per-submission logic as
# bot_sheet_sync.py's fetch_leetcode(), but stops after a bounded
# time/page budget instead of walking the user's ENTIRE history in one
# call, and returns exactly where it left off so the next request can
# resume from that offset.
# ===============================================================

def fetch_leetcode_chunk(lc_sess, start_offset):
    """Returns (rows, next_offset, has_more, error, cache_hits, cache_misses).

    Never restarts from 0 — the caller passes in the saved offset and
    persists next_offset only after this chunk's DB+Sheet writes succeed.
    """
    rows = []
    lc_seen_this_chunk = set()

    details_cache = _load_lc_details_cache()
    new_cache_entries = {}
    cache_hits = 0
    cache_misses = 0

    offset = start_offset
    error = None
    has_more = True
    chunk_start = time.time()
    pages_fetched = 0

    print(f"[LEETCODE] OFFSET/CURSOR: {start_offset}")

    while True:
        if pages_fetched >= LEETCODE_CHUNK_MAX_PAGES:
            print(f"[LEETCODE] chunk page cap reached ({LEETCODE_CHUNK_MAX_PAGES} pages), pausing at offset={offset}")
            break
        if time.time() - chunk_start >= LEETCODE_CHUNK_TIME_BUDGET_SECONDS:
            print(f"[LEETCODE] chunk time budget reached ({LEETCODE_CHUNK_TIME_BUDGET_SECONDS}s), pausing at offset={offset}")
            break

        try:
            r = lc_sess.get(
                "https://leetcode.com/api/submissions/",
                params={"offset": offset, "limit": LEETCODE_PAGE_LIMIT},
                timeout=(5, 10),
            )
        except Exception as e:
            error = str(e)
            break

        if r.status_code == 401:
            error = "401 unauthorized"
            break
        if r.status_code == 403:
            error = f"403 at offset {offset} — cookie expired"
            break
        if r.status_code != 200:
            error = f"HTTP {r.status_code}"
            break

        data = r.json()
        subs = data.get("submissions_dump", [])
        pages_fetched += 1

        if not subs:
            has_more = False
            break

        for sub in subs:
            if sub.get("status_display") != "Accepted":
                continue
            slug = sub.get("title_slug", "")
            if not slug or slug in lc_seen_this_chunk:
                continue
            lc_seen_this_chunk.add(slug)

            ts = sub.get("creationTimeSeconds") or sub.get("timestamp")
            if not ts:
                continue

            date = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            title = sub.get("title", slug)

            cached = details_cache.get(slug)
            if cached:
                difficulty, topic = cached[0], cached[1]
                cache_hits += 1
            else:
                difficulty, topic = get_leetcode_details(slug, session_obj=lc_sess)
                time.sleep(0.35)
                new_cache_entries[slug] = [difficulty, topic]
                cache_misses += 1

            problem_link = f"https://leetcode.com/problems/{slug}/"
            submission_link = f"https://leetcode.com/submissions/detail/{sub['id']}/"
            title_cell = f'=HYPERLINK("{problem_link}", "{title}")'

            rows.append([date, title_cell, submission_link, difficulty, "LeetCode", topic, int(ts)])

        if not data.get("has_next", False):
            has_more = False
            break

        offset += LEETCODE_PAGE_LIMIT
        time.sleep(0.25)

    _save_lc_details_cache(new_cache_entries)

    next_offset = offset if has_more else offset  # offset already points at the next unread page either way
    print(f"[LEETCODE] FETCH COMPLETED")
    print(f"Rows fetched: {len(rows)}")
    print(f"[LEETCODE] HAS_MORE: {has_more}")
    print(f"[LEETCODE] NEXT_OFFSET/CURSOR: {next_offset}")
    return rows, next_offset, has_more, error, cache_hits, cache_misses


def fetch_leetcode_cookie_from_db(user_id):
    with get_db() as db:
        row = db.execute(
            "SELECT lc_session_cookie, lc_csrf_token FROM users WHERE id=%s", (user_id,)
        ).fetchone()
    if not row:
        raise Exception("User not found in DB")
    session_cookie = row["lc_session_cookie"]
    csrf = row["lc_csrf_token"]
    if not session_cookie:
        raise Exception("No LEETCODE_SESSION cookie found")
    return session_cookie, csrf


# ===============================================================
# DEDUPE + POSTGRES BATCH WRITE — same drop_duplicates/groupby +
# execute_values approach as bot_sheet_sync.py, just scoped to
# whatever rows a single platform's fetch/chunk just returned instead
# of a 3-platform combined batch.
# ===============================================================

def dedupe_platform_rows(rows):
    """rows: list of [date, title_cell, link, difficulty, platform, topic, epoch].
    Returns a pandas DataFrame, deduped on (DATE, PROGRAM TITLE, PLATFORM) —
    same key bot_sheet_sync.py already used."""
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "EPOCH"])
    df = df.drop_duplicates(subset=["DATE", "PROGRAM TITLE", "PLATFORM"])
    return df


def _extract_problem_id(problem_url):
    if "leetcode.com/problems/" in problem_url:
        return problem_url.split("/problems/")[-1].split("/")[0]
    elif "codeforces.com/problemset/problem" in problem_url:
        parts = problem_url.split("/")
        return parts[-2] + "-" + parts[-1]
    elif "atcoder.jp" in problem_url:
        return problem_url.split("/")[-1]
    return problem_url


def write_submissions_batch(user_id, df):
    """df: deduped DataFrame from dedupe_platform_rows(). Same batched
    execute_values(...) ON CONFLICT DO NOTHING insert as bot_sheet_sync.py.
    Returns db_rows_written (actually-new rows, not just rows sent)."""
    if df is None or df.empty:
        return 0

    insert_params = []
    for row in df.values.tolist():
        title_cell = row[1]
        problem_url = ""
        if "HYPERLINK(" in title_cell:
            problem_url = title_cell.split('"')[1]
        submission_url = row[2]
        title = (
            title_cell.split('", "')[-1].rstrip('")')
            if "HYPERLINK(" in title_cell
            else title_cell
        )
        problem_id = _extract_problem_id(problem_url)

        epoch = row[6] if len(row) > 6 else None
        submitted_at = datetime.fromtimestamp(int(epoch)) if epoch is not None and not pd.isna(epoch) else None

        insert_params.append((
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

    insert_sql = """
INSERT INTO submissions
(user_id, problem_name, problem_id, problem_url, submission_url, platform, difficulty, tags, solved_date, submitted_at, solved_on)
VALUES %s
ON CONFLICT (user_id, platform, problem_id, solved_on)
DO NOTHING
RETURNING id
"""
    conn = get_db()
    try:
        cursor = conn.cursor()
        try:
            returned = psycopg2.extras.execute_values(
                cursor._cur, insert_sql, insert_params, page_size=500, fetch=True
            )
            new_count = len(returned) if returned is not None else 0
        except AttributeError:
            print("⚠️ execute_values fast path unavailable, falling back to per-row insert")
            new_count = 0
            for params in insert_params:
                cursor.execute("""
INSERT INTO submissions
(user_id, problem_name, problem_id, problem_url, submission_url, platform, difficulty, tags, solved_date, submitted_at, solved_on)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (user_id, platform, problem_id, solved_on)
DO NOTHING
""", params)
                if cursor.rowcount > 0:
                    new_count += 1
        conn.commit()
    finally:
        conn.close()

    print("[DATABASE] BATCH WRITE COMPLETED")
    print(f"Rows written: {new_count}")
    return new_count


# ===============================================================
# GOOGLE SHEET — rebuild the user's tab from the CURRENT FULL Postgres
# state (all platforms). Reuses the exact same clear+append_rows+merge
# batch-write approach as bot_sheet_sync.py's write_sheet_import(), just
# fed from a DB query instead of "this run's fetch" — see module
# docstring for why.
# ===============================================================

def _with_retry(fn, *args, retries=4, **kwargs):
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


def _write_sheet_rebuild(user_sheet, sheet_rows):
    """sheet_rows: list of [DATE(dd-mm-yyyy), TITLE_CELL, LINK, DIFFICULTY, PLATFORM, TOPIC, COUNT].
    Identical clear/rewrite/merge logic to bot_sheet_sync.py's write_sheet_import(),
    except the actual data write uses explicit cell ranges (A2, A502, ...) instead
    of append_row/append_rows.

    Why explicit ranges: user_sheet.clear() only wipes cell VALUES — it does not
    remove merged cells left over from the previous rebuild's regroup step (DATE
    and COUNT columns get merged for consecutive same-date rows). append_row /
    append_rows ask the Sheets API "where does the existing table end?", and a
    leftover empty merged range still counts as part of the table — so new rows
    silently got appended starting AFTER that stale merge instead of at row 2,
    i.e. the rebuild kept "continuing from the last position" instead of
    starting fresh. Unmerging first removes that trap; writing to explicit
    ranges removes the auto-detection entirely so it can't happen again even if
    unmerge itself fails for some other reason.
    """
    _with_retry(user_sheet.clear)

    try:
        _with_retry(
            user_sheet.spreadsheet.batch_update,
            {"requests": [{"unmergeCells": {"range": {"sheetId": user_sheet.id}}}]},
        )
    except Exception as e:
        # Don't swallow this: if unmerge fails, stale merged ranges from the
        # previous rebuild can still fool row auto-detection later, so a
        # rebuild must be allowed to fail loudly here rather than silently
        # writing rows into the wrong position.
        print(f"❌ Could not unmerge cells before Sheet rebuild for '{user_sheet.title}': {e}")
        raise

    _with_retry(
        user_sheet.update,
        "A1:G1",
        [["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"]],
        value_input_option="USER_ENTERED",
    )

    if sheet_rows:
        # Explicit row numbers, chunked, so this never depends on the Sheets
        # API's guess about where the "table" ends.
        CHUNK = 500
        next_row = 2
        for i in range(0, len(sheet_rows), CHUNK):
            chunk = sheet_rows[i:i + CHUNK]
            _with_retry(
                user_sheet.update,
                f"A{next_row}",
                chunk,
                value_input_option="USER_ENTERED",
            )
            next_row += len(chunk)

    time.sleep(1)

    all_values = user_sheet.get_all_values()
    date_rows = defaultdict(list)
    for row_num, row in enumerate(all_values[1:], start=2):
        if not row:
            continue
        date_val = str(row[0]).strip()
        if date_val:
            date_rows[date_val].append(row_num)

    requests_batch = []
    for date_val, rows in date_rows.items():
        if len(rows) <= 1:
            continue
        for col_start, col_end in ((0, 1), (6, 7)):  # DATE column, COUNT column
            requests_batch.append({
                "mergeCells": {
                    "range": {
                        "sheetId": user_sheet.id,
                        "startRowIndex": rows[0] - 1,
                        "endRowIndex": rows[-1],
                        "startColumnIndex": col_start,
                        "endColumnIndex": col_end,
                    },
                    "mergeType": "MERGE_ALL",
                }
            })

    if requests_batch:
        _with_retry(user_sheet.spreadsheet.batch_update, {"requests": requests_batch})


def rebuild_user_sheet(user_id, username, user_email, cohort_year):
    """Queries ALL of this user's submissions from Postgres (source of
    truth) and rewrites their Sheet tab to match — one batched clear +
    write, same as the original full-import Sheet write, just re-derived
    from the DB instead of from a single run's fetch. Returns
    sheet_rows_written. Raises on failure (caller decides how to report
    it — a Sheet failure never rolls back the DB write that already
    succeeded).

    Runs normal_sync.dedupe_blank_link_submissions() first — this
    function is called after EVERY /import_codeforces, /import_atcoder,
    and /import_leetcode request, so without that it would keep faithfully
    reproducing any old blank-link "ghost" duplicate rows on every single
    import, forever.

    Sorts on the actually-parsed date (utils.helpers._parse_any_date)
    instead of a raw `ORDER BY solved_date` text sort — solved_date isn't
    stored in one uniform format across all rows (older rows: DD-MM-YYYY
    text, newer: YYYY-MM-DD), so a text sort clumps whichever format is a
    minority together in the wrong place instead of interleaving
    everything in real chronological order.
    """
    from normal_sync import dedupe_blank_link_submissions
    from utils.helpers import _parse_any_date

    dedupe_blank_link_submissions(user_id, get_db)

    with get_db() as db:
        rows = db.execute(
            """SELECT solved_date, problem_name, problem_url, submission_url, difficulty, platform, tags
               FROM submissions WHERE user_id=%s""",
            (user_id,),
        ).fetchall()

    parsed = []
    for r in rows:
        dt = _parse_any_date(r["solved_date"])
        parsed.append((dt or datetime.min, r))
    parsed.sort(key=lambda t: t[0])

    sheet_rows = []
    for dt, r in parsed:
        date_ddmmyyyy = dt.strftime("%d-%m-%Y") if dt != datetime.min else (r["solved_date"] or "")
        title_cell = f'=HYPERLINK("{r["problem_url"]}", "{r["problem_name"]}")' if r["problem_url"] else (r["problem_name"] or "")
        sheet_rows.append([
            date_ddmmyyyy, title_cell, r["submission_url"] or "", r["difficulty"] or "",
            r["platform"] or "", r["tags"] or "", 1,
        ])

    # COUNT column = how many rows share that date (same semantics as the
    # original pandas groupby("DATE")["PROGRAM TITLE"].transform("count")).
    date_counts = defaultdict(int)
    for row in sheet_rows:
        date_counts[row[0]] += 1
    for row in sheet_rows:
        row[6] = date_counts[row[0]]

    if not cohort_year:
        raise RuntimeError(
            f"'{username}' has no year/cohort assigned yet — data was saved to the database, "
            "but the Google Sheet write is skipped until a mentor assigns a year."
        )

    print(f"⏳ [{username}] waiting for sheet-sync turn...")
    with FileLock(SHEET_LOCK_PATH, timeout=300):
        spreadsheet = open_year_spreadsheet(cohort_year)

        try:
            user_sheet = spreadsheet.worksheet(username)
        except gspread.exceptions.WorksheetNotFound:
            user_sheet = spreadsheet.add_worksheet(title=username, rows="5000", cols="10")
            user_sheet.append_row(["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"])

        secure_user_tab(spreadsheet, user_sheet, user_email, CREDENTIALS_FILE)

        print("[GOOGLE SHEET] BATCH WRITE STARTED")
        _write_sheet_rebuild(user_sheet, sheet_rows)
        print("[GOOGLE SHEET] BATCH WRITE COMPLETED")
        print(f"Rows written: {len(sheet_rows)}")

    return len(sheet_rows)


def get_user_row(user_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
    return dict(row) if row else None
