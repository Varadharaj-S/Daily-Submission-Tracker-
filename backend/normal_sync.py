from collections import Counter
import os
import json
import time
from datetime import datetime

import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from utils.helpers import solved_on_iso

# ================= CONFIG =================

# PHASE 2: the old shared SHEET_ID constant is gone — spreadsheet IDs now
# live in the year_sheets DB table, resolved per-student via
# services/year_sheet_service.py. CREDENTIALS_FILE (local-dev fallback
# only; GOOGLE_SERVICE_JSON env var is preferred) stays here since
# several other modules still import it from this file.
CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"
LC_CACHE_FILE = "lc_cache.json"

# ================= GOOGLE SHEETS =================

def get_sheet(username, year):
    """Get or create a per-student worksheet tab inside that STUDENT'S
    YEAR spreadsheet (PHASE 2: one spreadsheet per year/cohort, resolved
    via services/year_sheet_service.py — no more single shared SHEET_ID).
    `year` must already be a trusted value — the caller's own
    cohort_year from the DB, never client-supplied input."""
    from services.year_sheet_service import open_year_spreadsheet
    spreadsheet = open_year_spreadsheet(year)

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
    values = sheet.get_all_values()

    if len(values) <= 1:
        return

    values = values[1:]   # Skip header

    # Group rows into CONTIGUOUS blocks of the SAME date value — i.e. a
    # new block starts whenever this row's date differs from the row
    # directly above it, not whenever column A happens to be non-blank.
    #
    # BUGFIX: the previous version treated ANY non-blank column-A cell as
    # the start of a new block (blank = "continue the block above"). But
    # every write path in this file (fetch_cf/fetch_lc/fetch_ac, and
    # rebuild_user_sheet_from_db) writes the FULL date on every single
    # row — nothing ever leaves column A blank on first write. So every
    # row always started its own 1-row block, "len(rows) == 1: continue"
    # skipped every one, and merging never fired at all on freshly written
    # data (only worked, by accident, on a sheet a previous merge had
    # already blanked out).
    #
    # This version compares each row's date to the row immediately above
    # it (whether that cell is blank from a prior merge, or fully written
    # text) — so genuinely adjacent same-date rows merge correctly, while
    # non-adjacent blocks that happen to share the same date string (e.g.
    # today's date reappearing lower down after a later sync appended more
    # rows below other, different dates) still stay separate, which is the
    # exact regression the old comment above was originally guarding
    # against.
    date_blocks = []
    current_block = []
    prev_date = None
    for row_no, row in enumerate(values, start=2):
        date_val = row[0].strip() if row else ""
        if not date_val:
            # Blank cell — already part of a merged block above; keep it
            # in the current block regardless of prev_date.
            current_block.append(row_no)
            continue
        if date_val == prev_date:
            current_block.append(row_no)
        else:
            if current_block:
                date_blocks.append(current_block)
            current_block = [row_no]
            prev_date = date_val
    if current_block:
        date_blocks.append(current_block)

    # BUGFIX: this used to fire ONE single batch_update with every merge
    # request for the whole sheet in it. On a sheet with a lot of history
    # that's potentially hundreds of mergeCells requests in one call — if
    # the Sheets API rejected/timed out on that single oversized call (a
    # transient 429/500/503, or just the request getting too big), NOTHING
    # merged: all-or-nothing failure. Chunking + retrying below means a
    # transient blip only costs one small chunk, and one bad chunk can't
    # take down the rest of the sheet's merges with it.
    def _send_batch_update(reqs, retries=4):
        """POST one batch_update, retrying on transient Sheets API errors
        (429 rate limit, 500/503 transient server errors) with backoff.
        Returns True on success, False if it ultimately failed."""
        for attempt in range(retries):
            try:
                sheet.spreadsheet.batch_update({"requests": reqs})
                return True
            except gspread.exceptions.APIError as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (429, 500, 503) and attempt < retries - 1:
                    wait = 2 ** attempt * 2
                    print(f"⚠️ Sheets API {status} on merge batch, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"Merge error: {e}")
                return False
            except Exception as e:
                print(f"Merge error: {e}")
                return False
        return False

    _send_batch_update([{"unmergeCells": {"range": {"sheetId": sheet.id}}}])

    requests = []

    for rows in date_blocks:

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

    # Chunked sends: small enough per call to stay well under the Sheets
    # API's per-request size/time limits, and independent enough that one
    # failed chunk (after its own retries) doesn't block the others —
    # partial success beats total failure on a big sheet.
    MERGE_CHUNK_SIZE = 100
    if requests:
        ok_count = 0
        fail_count = 0
        for i in range(0, len(requests), MERGE_CHUNK_SIZE):
            chunk = requests[i:i + MERGE_CHUNK_SIZE]
            if _send_batch_update(chunk):
                ok_count += len(chunk)
            else:
                fail_count += len(chunk)
        if fail_count:
            print(f"⚠️ regroup_sheet: {fail_count} merge request(s) failed after retries, {ok_count} succeeded")

def backfill_missing_rows_from_db(user_id, username, get_db, year):
    """
    Safe alternative to rebuild_user_sheet_from_db: find rows that exist in
    Postgres but are missing from the sheet (matched by problem URL) and
    APPEND only those. The sheet is NEVER cleared, so there is no failure
    mode - Google API error, network blip, or the Vercel function getting
    killed on timeout mid-write - that can leave the sheet empty. Use this
    instead of the full rebuild to catch the sheet up with rows that got
    stuck in the DB from an earlier failed sync.
    `year` is the student's cohort year (PHASE 2) — resolves which
    year-specific spreadsheet this student's tab lives in.
    """
    sheet = get_sheet(username, year)
    existing = sheet.get_all_values()
    existing_urls = set(
        r[2].strip() for r in existing[1:] if len(r) > 2 and r[2].strip()
    )

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

    missing = [s for s in subs if (s.get("problem_url") or "") not in existing_urls]
    if not missing:
        print(f"✅ '{username}' sheet already has everything in the DB — nothing to backfill")
        return 0

    from utils.helpers import _parse_any_date

    parsed = []
    for s in missing:
        dt = _parse_any_date(s["solved_date"])
        parsed.append((dt or datetime.min, s["id"], s))
    parsed.sort(key=lambda t: (t[0], t[1]))

    rows = []
    for dt, _id, s in parsed:
        title = s.get("problem_name") or "Unknown"
        url = s.get("problem_url") or ""
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

    # Write at an EXACT row number we compute ourselves, instead of
    # append_rows()'s auto-detected "next empty row" — merged DATE cells
    # (from regroup_sheet) leave blanks in column A that can trick that
    # auto-detection into inserting mid-sheet instead of at the true end.
    next_row = len(existing) + 1
    CHUNK = 300
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        sheet.update(f"A{next_row}", chunk, value_input_option="USER_ENTERED")
        next_row += len(chunk)
        print(f"📄 Backfilled {min(i + CHUNK, len(rows))}/{len(rows)} rows for '{username}'")

    # BUGFIX (date order / merge collapse) — same reasoning as
    # append_new_rows_to_sheet: this backfill also always writes at the
    # bottom, so missing historical rows getting caught up here can land
    # below dates that are actually later. Re-sort the whole body by parsed
    # date before recomputing COUNT/regroup so everything merges correctly.
    _resort_sheet_body_by_date(sheet, username, step_label="backfill-resort")

    try:
        values = sheet.get_all_values()
        current_date = ""
        date_col = []
        for row in values[1:]:
            if row and row[0].strip():
                current_date = row[0].strip()
            date_col.append(current_date)
        counts = Counter(date_col)
        count_column = [[counts[d]] for d in date_col]
        sheet.update(
            f"G2:G{len(values)}",
            count_column,
            value_input_option="USER_ENTERED"
        )
        regroup_sheet(sheet)
    except Exception as e:
        print(f"⚠️ Count/regroup step failed for '{username}' after backfill (rows still saved): {e}")

    print(f"✅ Backfilled {len(rows)} missing rows into '{username}' sheet")
    return len(rows)


def rebuild_user_sheet_from_db(user_id, username, get_db, year):
    """
    Wipe this user's Google Sheet tab and rewrite it completely from
    PostgreSQL. PostgreSQL is always the source of truth — this is the
    "someone deleted rows in the Sheet, get them back" button.
    `year` is the student's cohort year (PHASE 2) — resolves which
    year-specific spreadsheet this student's tab lives in.
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
    
    sheet = get_sheet(username, year)
    # If the write below fails halfway (network hiccup, Google API quota,
    # a bad row, etc.) we restore this snapshot instead of leaving the
    # sheet cleared — a rebuild must never be able to make things worse
    # than before it ran.
    try:
        snapshot = sheet.get_all_values()
    except Exception as e:
        print(f"⚠️ Could not snapshot '{username}' sheet before rebuild: {e}")
        snapshot = None

    try:
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

            # Write in chunks so one oversized request can't fail the whole
            # batch on sheets with a lot of history. Explicit row numbers,
            # not append_rows()'s auto-detection (see notes above).
            CHUNK = 500
            next_row = 2
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                sheet.update(f"A{next_row}", chunk, value_input_option="USER_ENTERED")
                next_row += len(chunk)

            # regroup: merge DATE and COUNT cells for consecutive same-date rows
            regroup_sheet(sheet)

    except Exception as e:
        print(f"❌ Rebuild failed mid-write for '{username}': {e}")
        if snapshot:
            print(f"↩️  Restoring previous sheet content for '{username}' ({len(snapshot)} rows)")
            try:
                sheet.batch_clear(["A:Z"])
                if snapshot:
                    sheet.update("A1", snapshot, value_input_option="USER_ENTERED")
            except Exception as e2:
                print(f"❌ Restore-after-failure ALSO failed for '{username}': {e2}")
        raise

    print(f"🔄 Restored '{username}' sheet from DB — {len(rows)} rows")
    return len(rows)


def _resort_sheet_body_by_date(sheet, username, step_label="resort"):
    """Shared by append_new_rows_to_sheet and backfill_missing_rows_from_db:
    re-sort the sheet body (everything below the header) by parsed date and
    write the exact same rows back in place, so a batch appended at the
    bottom (which may be older than rows already above it) collapses back
    into its correct chronological position and merges with any existing
    same-date block instead of sitting apart from it. Non-destructive — same
    row count, only reordered. See append_new_rows_to_sheet's docstring for
    the full story on why this exists."""
    try:
        from utils.helpers import _parse_any_date

        values = sheet.get_all_values()
        if len(values) <= 1:
            return

        body = values[1:]

        # Forward-fill column A (DATE): merged date-group rows have that
        # cell blank on every row but the first in their block, and
        # re-sorting rows individually would otherwise orphan those blanks
        # from the date they actually belong to.
        filled = []
        current_date = ""
        for row in body:
            row = list(row) + [""] * (7 - len(row)) if len(row) < 7 else row[:7]
            if row[0].strip():
                current_date = row[0].strip()
            else:
                row[0] = current_date
            filled.append(row)

        def sort_key(row):
            dt = _parse_any_date(row[0])
            return dt or datetime.max  # unparseable dates sort last, not first

        filled.sort(key=sort_key)

        sheet.update(f"A2:G{len(values)}", filled, value_input_option="USER_ENTERED")
    except Exception as e:
        # Rows are already written and safe — only the reordering failed.
        print(f"⚠️ Date {step_label} step failed for '{username}' (rows still saved): {e}")


def append_new_rows_to_sheet(username, new_rows, year):
    """
    Non-destructive path used by every normal "Sync Now" call: only APPEND
    the rows that are new this run, then fix up the COUNT column and the
    merged-cell grouping. This never clears/wipes the sheet, so a failure
    here can't collapse existing data — worst case the new rows just don't
    show up yet, and they're already safe in Postgres for the next sync or
    an explicit "Restore sheet" to pick up.
    `year` is the student's cohort year (PHASE 2) — resolves which
    year-specific spreadsheet this student's tab lives in.
    """
    sheet = get_sheet(username, year)

    # Use col B (problem title — always filled, never blank from a merge)
    # to find the true last used row.  Col A is unreliable because
    # regroup_sheet() blanks it for merged date-group rows, so
    # len(get_all_values()) over-counts and places new rows on top of
    # existing ones when the sheet has any merged date blocks.
    existing = sheet.get_all_values()
    # Walk from the bottom to find the last row that has any non-blank cell.
    last_used = 0
    for i, row in enumerate(existing, start=1):
        if any(cell.strip() for cell in row):
            last_used = i
    next_row = last_used + 1

    sheet.update(f"A{next_row}", new_rows, value_input_option="USER_ENTERED")

    # BUGFIX (date order / merge collapse): this used to stop here and go
    # straight to COUNT/regroup. That's exactly why old sheets ended up with
    # dates all over the place and identical dates refusing to merge: each
    # sync's batch only ever got tacked onto the very bottom of the sheet,
    # regardless of whether those dates were older than rows already sitting
    # above (e.g. a Codeforces solve from 3 days ago surfacing in today's
    # fetch). Over many syncs the sheet degenerates into dozens of
    # non-adjacent same-date blocks — and regroup_sheet() only ever merges
    # CONTIGUOUS same-date rows by design (see its own comment), so all of
    # those scattered duplicates stayed unmerged and visibly "everywhere".
    #
    # Fix: after appending (still non-destructive — nothing is cleared, no
    # row count changes), re-sort the WHOLE sheet body by parsed date and
    # write the exact same rows back in place, in date order. Same-date rows
    # collapse back into one contiguous block wherever they came from, so
    # every date merges correctly again, and the sheet reads chronologically
    # top to bottom like it's supposed to. Ties keep their original relative
    # order (Python's sort is stable) — untouched history isn't reshuffled
    # for no reason, only re-grouped by date.
    _resort_sheet_body_by_date(sheet, username, step_label="resort")

    # Recompute COUNT (col G) for every date across the sheet without
    # touching columns A-F — purely additive/overwrite on one column.
    try:
        values = sheet.get_all_values()
        if len(values) > 1:
            current_date = ""
            date_col = []
            for row in values[1:]:
                if row and row[0].strip():
                    current_date = row[0].strip()
                date_col.append(current_date)

            counts = Counter(date_col)
            count_column = [[counts[d]] for d in date_col]
            sheet.update(
                f"G2:G{len(values)}",
                count_column,
                value_input_option="USER_ENTERED"
            )

        regroup_sheet(sheet)
    except Exception as e:
        # Rows are already written and safe — only the COUNT/merge
        # formatting failed, so just log it instead of failing the sync.
        print(f"⚠️ Count/regroup step failed for '{username}' (rows still saved): {e}")

    return len(new_rows)


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

def epoch_seconds_from_ts(ts):
    """Same normalization as parse_timestamp, but returns raw epoch seconds
    (int) instead of a formatted date string — used for submissions.submitted_at.
    Returns None if ts isn't a numeric timestamp (e.g. LC gave us a date
    string instead) since we can't derive an exact time from that."""
    try:
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if isinstance(ts, (int, float)):
            if ts > 10**12:
                ts = ts / 1000
            return int(ts)
    except Exception:
        pass
    return None

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
        epoch = sub["creationTimeSeconds"]
        date = datetime.fromtimestamp(epoch).strftime("%d-%m-%Y")


        rows.append([
            date,
            f'=HYPERLINK("{url}", "{title}")',
            sub_link,
            diff,
            "Codeforces",
            topic,
            1,
            epoch,  # hidden 8th element: raw epoch seconds, used for submissions.submitted_at (stripped before writing to the sheet — see sync_user_data)
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

_ALFA_BASE = "https://alfa-leetcode-api.onrender.com"

def alfa_wakeup():
    """
    One-shot wake-up ping for alfa-leetcode-api (free Render dyno).
    Call ONCE per sync batch — NOT once per user or per endpoint.
    """
    try:
        ping = requests.get(_ALFA_BASE, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        print(f"[LC] alfa wake-up ping: {ping.status_code}")
    except Exception as e:
        print(f"[LC] alfa wake-up ping failed: {e}")


def _fetch_alfa_with_retry(endpoint, max_retries=3):
    """
    GET from alfa-leetcode-api with 429 backoff retry.
    alfa_wakeup() should already have been called once before this batch.
    Returns (data_dict_or_list, err_str_or_None).
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    delay = 5  # seconds between retries
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.get(endpoint, headers=headers, timeout=30)
            if res.status_code == 200:
                ctype = res.headers.get("Content-Type", "")
                if "json" not in ctype.lower():
                    return None, f"non-json content-type={ctype}"
                return res.json(), None
            if res.status_code == 429:
                print(f"[LC] 429 rate-limited (attempt {attempt}/{max_retries}), "
                      f"sleeping {delay}s…")
                time.sleep(delay)
                delay *= 2
                continue
            return None, f"status={res.status_code}"
        except Exception as e:
            return None, str(e)

    return None, "status=429 (all retries exhausted)"


def _fetch_lc_via_graphql(lc_handle, seen, limit=20):
    """
    Fallback: hit LeetCode's own public GraphQL with recentAcSubmissionList.
    No auth required for public profiles. Returns sheet rows (same format
    as the alfa path), skipping any slug already in `seen`.
    """
    LC_GQL = "https://leetcode.com/graphql"
    query = """
    query recentAC($username: String!, $limit: Int!) {
      recentAcSubmissionList(username: $username, limit: $limit) {
        id
        title
        titleSlug
        timestamp
      }
    }
    """
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        res = requests.post(
            LC_GQL,
            json={"query": query, "variables": {"username": lc_handle, "limit": limit}},
            headers=headers,
            timeout=30,
        )
        if res.status_code != 200:
            print(f"[LC-GQL] failed: status={res.status_code}")
            return []
        subs = (res.json().get("data") or {}).get("recentAcSubmissionList") or []
    except Exception as e:
        print(f"[LC-GQL] exception: {e}")
        return []

    rows = []
    for sub in subs:
        slug = sub.get("titleSlug") or ""
        if not slug or slug in seen:
            continue
        seen.add(slug)

        title = sub.get("title") or slug
        ts = sub.get("timestamp") or int(time.time())
        date = parse_timestamp(ts)
        epoch = epoch_seconds_from_ts(ts)
        sub_id = sub.get("id") or ""
        problem_link = f"https://leetcode.com/problems/{slug}/"
        sub_link = f"https://leetcode.com/submissions/detail/{sub_id}/" if sub_id else problem_link
        diff, topic = get_lc_details(slug)
        rows.append([
            date,
            f'=HYPERLINK("{problem_link}", "{title}")',
            sub_link,
            diff,
            "LeetCode",
            topic,
            1,
            epoch,
        ])

    if rows:
        print(f"[LC-GQL] fetched {len(rows)} rows via direct GraphQL")
    return rows


def fetch_lc(lc_handle):
    if not lc_handle:
        print("[LC] No handle set, skipping.")
        return []

    print("[LC] Starting no-cookie fetch...")
    alfa_wakeup()

    rows = []
    seen = set()
    cached_rows = load_cache(LC_CACHE_FILE)

    endpoints = [
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/acSubmission?limit=20",
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/submission?limit=20",
    ]

    for endpoint in endpoints:
        try:
            data, err = _fetch_alfa_with_retry(endpoint, max_retries=3)
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
                epoch = epoch_seconds_from_ts(ts)

                sub_id = (
                    sub.get("id")
                    or sub.get("submissionId")
                    or sub.get("submission_id")
                    or ""
                )

                problem_link = f"https://leetcode.com/problems/{slug}/"
                sub_link = f"https://leetcode.com/submissions/detail/{sub_id}/" if sub_id else problem_link

                diff, topic = get_lc_details(slug)
                temp_rows.append([
                    date,
                    f'=HYPERLINK("{problem_link}", "{title}")',
                    sub_link,
                    diff,
                    "LeetCode",
                    topic,
                    1,
                    epoch,
                ])

            if temp_rows:
                rows = temp_rows
                print(f"[LC] fetched {len(rows)} rows from alfa endpoint")
                break

        except Exception as e:
            print(f"[LC] endpoint exception: {e}")

    # ── Fallback: direct LeetCode GraphQL ──────────────────────────────────
    if not rows:
        print("[LC] alfa endpoints failed — trying direct GraphQL fallback…")
        rows = _fetch_lc_via_graphql(lc_handle, seen)

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
            sub["epoch_second"],  # hidden 8th element: see fetch_cf comment
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
    year      = (user.get("cohort_year") or "").strip() or None

    print(f"\n🔄 Syncing for user: {username}")
    print(f"   CF={cf_handle or '(none)'}  LC={lc_handle or '(none)'}  AC={ac_handle or '(none)'}")

    all_data = []
    platform_errors = []

    # BUGFIX (per-user sync robustness): each fetch_* used to run
    # sequentially with no isolation — fetch_cf/fetch_lc/fetch_ac mostly
    # catch their own network/API errors internally and return [], but an
    # unexpected exception (a malformed API record, a KeyError on a
    # missing field, etc.) in ANY one of them used to propagate straight
    # out of sync_user_data and abort the whole run — so a single bad CF
    # submission could silently stop that user's LC and AC from ever being
    # fetched too, with no trace beyond a crashed background thread. Each
    # platform now gets its own try/except: one platform failing can never
    # take the other two down with it, and the failure is recorded (both
    # printed and returned) instead of swallowed.
    for label, fn, handle in (
        ("CF", fetch_cf, cf_handle),
        ("LC", fetch_lc, lc_handle),
        ("AC", fetch_ac, ac_handle),
    ):
        print(f"Fetching {label}...")
        try:
            all_data.extend(fn(handle))
        except Exception as e:
            print(f"[{label} ERROR] sync_user_data: unexpected failure, continuing with other platforms: {e}")
            platform_errors.append(f"{label} failed: {e}")

    # BUGFIX: this used to return here without touching the DB at all,
    # which meant users.last_sync never got written by this code path —
    # so the auto-sync scheduler had no way to tell "already synced
    # today" from "never synced" (see utils/sync_schedule.py). Stamp
    # last_sync on every run, even a no-new-data run.
    now_iso = datetime.utcnow().isoformat(timespec="seconds")

    if not all_data:
        conn = get_db()
        conn.execute("UPDATE users SET last_sync=? WHERE id=?", (now_iso, user["id"]))
        conn.commit()
        conn.close()
        msg = "No data fetched"
        if platform_errors:
            msg += " (" + "; ".join(platform_errors) + ")"
        return {"success": False, "message": msg, "new_count": 0}

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

        # Hidden 8th element (see fetch_cf/fetch_lc/fetch_ac) — raw epoch
        # seconds when available, so contest grading can tell in-window
        # vs after-window solves. None for legacy/cache rows without it.
        epoch = row[7] if len(row) > 7 else None
        submitted_at = datetime.fromtimestamp(epoch) if epoch else None

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
                    solved_date,
                    submitted_at,
                    solved_on
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, platform, problem_id, solved_on) DO NOTHING
                """, (
                    user_id,
                    title,
                    problem_id,
                    problem_url,
                    platform,
                    row[3],      # difficulty
                    row[5],      # <-- TAGS
                    row[0],      # solved_date
                    submitted_at,
                    solved_on_iso(row[0]),   # real DATE mirror — see utils/helpers.py
                ))

        if cursor.rowcount > 0:
            new_count += 1
            # Sort key: prefer the raw epoch (exact, handles same-day
            # ordering across platforms); fall back to parsing the
            # DD-MM-YYYY solved_date string for legacy/cache rows that
            # don't carry one. Keep the key out-of-band — the sheet's
            # 7-column row format (row[:7]) still drops the hidden epoch.
            if epoch:
                sort_key = epoch
            else:
                try:
                    sort_key = datetime.strptime(row[0], "%d-%m-%Y").timestamp()
                except Exception:
                    sort_key = 0  # unparseable date — sorts first rather than crashing
            new_rows.append((sort_key, row[:7]))

    cursor.execute("UPDATE users SET last_sync=%s WHERE id=%s", (now_iso, user_id))
    conn.commit()
    conn.close()

    # BUGFIX: all_data is CF rows, then LC rows, then AC rows, each already
    # in that platform's own order — but concatenated across platforms
    # that's NOT chronological (e.g. a Codeforces solve from days ago can
    # land after this run's LeetCode solves from today). append_new_rows_to_
    # sheet always writes to the true bottom of the sheet, so unsorted
    # new_rows showed up as out-of-order dates mixed into otherwise-sorted
    # history. Sort chronologically before writing so each sync's batch is
    # at least internally in date order.
    new_rows.sort(key=lambda pair: pair[0])
    new_rows = [row for _, row in new_rows]

    # Write new rows to this user's own tab in the Google Sheet.
    # This APPENDS only — it never clears the sheet, so a failure here
    # can't collapse existing rows. Full clear+rewrite is reserved for the
    # explicit "Restore sheet" admin action (rebuild_user_sheet_from_db).
    sheet_msg = None
    sheet_ok = True
    if new_rows:
        if not year:
            # PHASE 2: no cohort_year yet (unassigned student) — DB save
            # above already happened, just can't resolve a sheet. Don't
            # guess a spreadsheet; surface a clear, actionable message.
            sheet_ok = False
            sheet_msg = "⚠️ Data saved to database, but you're not assigned to a year/cohort yet — ask your mentor to set one so your sheet can sync."
        else:
            try:
                append_new_rows_to_sheet(username, new_rows, year)
                print(f"📄 Added {len(new_rows)} rows to tab '{username}' ({year} sheet)")
            except Exception as e:
                sheet_ok = False
                sheet_msg = f"⚠️ Data saved to database, but sheet update failed: {e}"
                print("Sheet error:", e)
    else:
        print(f"📄 No new rows for '{username}'")

    message = f"{new_count} new problems added"
    if sheet_msg:
        message += f" | {sheet_msg}"
    if platform_errors:
        # Partial success: some platform(s) fetched fine and got saved
        # above (new_count/new_rows reflect that), one or more others
        # threw. Surface it instead of a silent gap in the user's data.
        message += " | ⚠️ " + "; ".join(platform_errors)

    return {
        "success": True,
        "sheet_synced": sheet_ok,
        "message": message,
        "new_count": new_count,
        "platform_errors": platform_errors,
    }