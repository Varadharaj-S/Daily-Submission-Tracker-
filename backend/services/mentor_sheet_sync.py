"""
services/mentor_sheet_sync.py — mirrors mentor-assigned problems into the
master roster tab you already track by hand (the "SkillRack Sir Class
Track"-style tab shown in your screenshot: Reg No | Name |
Branch | Solved | <one column per problem, dropdown Solved/Not Solved>).
(Regn Num column removed — Reg No is the only ID column now.)

WHY THIS FILE EXISTS
---------------------
Before this, `/admin/mentor` only wrote rows into the Postgres
`mentor_assignments` table — nothing ever touched the actual Google Sheet.
Your screenshot's columns (ABC_443_E - Climbing Silver, CF_DIV2_1077_B -
Seats, ...) are a manually maintained tab that lives in the SAME
spreadsheet (SHEET_ID) as the app's per-student tabs, just a different
worksheet. This module automates exactly that pattern for problems
assigned through Mentor Mode:

  1. When an admin assigns a problem (single student or "assign to all"),
     find or create a column for that problem in the roster tab, and set
     each assigned student's cell to "Solved" / "Not Solved" (matching the
     dropdown + color-coded style already in your sheet).
  2. `resync_all()` re-checks every assignment against real submissions,
     updates any cell that changed, and appends a row for any active
     student who isn't in the roster yet (new user support).

ASSUMPTIONS I COULD NOT VERIFY LIVE
-------------------------------------
I have no network access to the Google Sheets API from where I'm running,
so none of this has been tested against your real sheet — only checked
for syntax/logical correctness. Please do one manual "Assign" + check the
sheet after deploying, same as you'd do for any new integration. Specific
assumptions, all overridable via environment variables so you don't have
to edit code if any are wrong:

  - MENTOR_SHEET_TAB   (default: "SkillRack Sir Class Track")
      Worksheet/tab name that holds the roster shown in your screenshot.
  - MENTOR_SHEET_KEY_HEADER  (default: "Reg No")
      Header text (row 3 in your screenshot) used to match a student's
      row. Falls back to "Regn Num" then to matching by Name if neither
      column is found.
  - Header layout: row 3 holds column labels (Regn Num, Reg No, Name,
      Branch, Solved, ...), data starts at row 4 — matches your screenshot
      exactly (rows 1-2 are merged "webinar date" banners). New problem
      columns get a plain single-row header (problem name, hyperlinked to
      the problem URL) written into row 3, directly above where data
      starts — adjust HEADER_ROW below if your real layout differs.
"""

import os
import re
import threading

import gspread
from google.oauth2.service_account import Credentials

from normal_sync import CREDENTIALS_FILE, SHEET_ID

# ── Config (env-overridable) ────────────────────────────────────────────────
MENTOR_SHEET_TAB = os.environ.get("MENTOR_SHEET_TAB", "SkillRack Sir Class Track")
KEY_HEADER_CANDIDATES = [
    os.environ.get("MENTOR_SHEET_KEY_HEADER", "Reg No"),
    "Regn Num", "Register Number", "RegNo", "Reg. No",
]
NAME_HEADER_CANDIDATES = ["Name", "Student Name"]
HEADER_ROW = int(os.environ.get("MENTOR_SHEET_HEADER_ROW", "3"))   # row with column labels
DATA_START_ROW = HEADER_ROW + 1                                    # first row of student data

STATUS_SOLVED = "Solved"
STATUS_NOT_SOLVED = "Not Solved"

_lock = threading.Lock()   # gspread client isn't guaranteed thread-safe; serialize writes


def _client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.getenv("GOOGLE_SERVICE_JSON"):
        import json
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(service_info, scopes=scope)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return gspread.authorize(creds)


ROSTER_BASE_HEADERS = ["Reg No", "Name", "Branch"]  # Regn Num column removed — Reg No is the key column; no summary "Solved" column — each problem column carries its own status


def _import_students_into_new_roster(ws, get_db):
    """Fills the freshly-created roster tab with one row per active,
    non-admin student (Reg No / Name / Branch), same 'first fill details'
    step contest_sheet.py's import_students() does for the contest tab.
    Returns the number of rows written."""
    if get_db is None:
        return 0
    with get_db() as db:
        students = db.execute(
            "SELECT reg_no, full_name, branch FROM users "
            "WHERE status='active' AND is_admin=0 ORDER BY full_name ASC"
        ).fetchall()
    rows = [[s["reg_no"] or "", s["full_name"] or "", s["branch"] or ""] for s in students]
    if rows:
        ws.update(f"A{DATA_START_ROW}", rows, value_input_option="USER_ENTERED")
    return len(rows)


def get_or_create_roster_ws(get_db=None):
    """Open the shared spreadsheet and return the roster worksheet,
    creating it (as a new tab in the SAME spreadsheet, matching how
    contest_sheet.py's get_or_create_student_contest_sheet() works) the
    first time a problem is ever assigned. On creation: writes the header
    row at HEADER_ROW and immediately fills in every active student's Reg
    No / Name / Branch — details go in first, problem columns get added
    on top of that by _find_or_create_problem_column(), same additive-only
    pattern as the contest tab's per-contest columns."""
    gc = _client()
    ss = gc.open_by_key(SHEET_ID)
    try:
        return ss.worksheet(MENTOR_SHEET_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=MENTOR_SHEET_TAB, rows="2000", cols="20")
        if HEADER_ROW > 1:
            ws.update_cell(1, 1, "Mentor Assigned Problems")  # banner row, mirrors the manual sheet's layout
        ws.update(f"A{HEADER_ROW}", [ROSTER_BASE_HEADERS], value_input_option="USER_ENTERED")
        _import_students_into_new_roster(ws, get_db)
        return ws


def _get_roster_ws(get_db=None):
    """Back-compat wrapper — now always gets-or-creates instead of
    returning None, so a missing tab no longer silently skips the sync."""
    return get_or_create_roster_ws(get_db)


def _col_letter(col_idx):
    letters = ""
    n = col_idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _header_row_values(ws):
    return ws.row_values(HEADER_ROW)


def _find_key_column(headers):
    """Return (col_index_1based, mode) where mode is 'reg_no' or 'name'."""
    for cand in KEY_HEADER_CANDIDATES:
        for i, h in enumerate(headers, start=1):
            if h.strip().lower() == cand.strip().lower():
                return i, "reg_no"
    for cand in NAME_HEADER_CANDIDATES:
        for i, h in enumerate(headers, start=1):
            if h.strip().lower() == cand.strip().lower():
                return i, "name"
    return None, None


def _build_row_index(ws, headers):
    """Map reg_no (or lowercased name, as fallback) -> sheet row number."""
    key_col, mode = _find_key_column(headers)
    if not key_col:
        return {}, None, None
    col_values = ws.col_values(key_col)  # includes header rows
    index = {}
    for i, val in enumerate(col_values[DATA_START_ROW - 1:], start=DATA_START_ROW):
        v = (val or "").strip()
        if not v:
            continue
        index[v.lower() if mode == "name" else v] = i
    return index, key_col, mode


def _norm_problem_key(problem_name, problem_url):
    """Loose key for matching an existing column to this problem, so
    re-assigning the same problem updates the same column instead of
    creating a duplicate."""
    base = (problem_url or problem_name or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", base)


def _strip_due_suffix(text):
    """Header text is written as 'Problem Name (Due 2026-08-15)' when a
    due date is set, and just 'Problem Name' when it isn't (e.g. from a
    plain Search). Strip that suffix before using the text as an identity
    key, or the same problem assigned with vs. without a due date reads
    as two different problems."""
    return re.sub(r"\s*\(Due[^)]*\)\s*$", "", text or "").strip()


def _header_row_formulas(ws):
    """Row HEADER_ROW's raw cell formulas (not the rendered display
    text), so a HYPERLINK(...) column's real underlying URL can be
    recovered for matching — row_values()/get_all_values() only ever
    return the rendered link text, never the URL itself."""
    try:
        rows = ws.get(f"{HEADER_ROW}:{HEADER_ROW}", value_render_option="FORMULA")
        return rows[0] if rows else []
    except Exception:
        return []


def _header_col_key(formula_text, display_text):
    """Canonical identity key for one existing header cell. Prefers the
    HYPERLINK formula's real URL (so the same problem always matches the
    same column no matter what due-date text sits next to it); falls
    back to the display text with any '(Due ...)' suffix stripped."""
    m = re.match(r'=HYPERLINK\("([^"]+)"', formula_text or "")
    if m:
        return _norm_problem_key("", m.group(1))
    return _norm_problem_key(_strip_due_suffix(display_text), "")


def repair_duplicate_problem_columns(ws):
    """Cleanup for the matching bug above: because _find_or_create_problem_column
    used to compare the target key (URL-preferring) against the header's
    raw display text (name + due-date suffix), the SAME problem could get
    a fresh column every time it was assigned with a different due date,
    or once via URL and once via name-only search. This merges every
    group of header columns that share a canonical key (via
    _header_col_key) back into one — the leftmost column is kept, 'Solved'
    wins over 'Not Solved'/blank across the group when merging each row —
    and the extra columns are deleted. Returns the number of columns
    removed. Safe to call every sync; a no-op once nothing is duplicated."""
    header = _header_row_values(ws)
    start = len(ROSTER_BASE_HEADERS)  # 0-based index where problem columns begin
    if len(header) <= start:
        return 0

    formulas = _header_row_formulas(ws)
    groups = {}
    for i in range(start + 1, len(header) + 1):  # 1-based column index
        h = header[i - 1]
        f = formulas[i - 1] if i - 1 < len(formulas) else ""
        key = _header_col_key(f, h)
        if key:
            groups.setdefault(key, []).append(i)

    dup_groups = {k: cols for k, cols in groups.items() if len(cols) > 1}
    if not dup_groups:
        return 0

    all_values = ws.get_all_values()
    data_rows = all_values[DATA_START_ROW - 1:]

    cols_to_delete = []
    for cols in dup_groups.values():
        primary = min(cols)
        merged = []
        for row in data_rows:
            vals = [(row[c - 1].strip() if c - 1 < len(row) else "") for c in cols]
            if STATUS_SOLVED in vals:
                merged.append(STATUS_SOLVED)
            else:
                primary_val = row[primary - 1].strip() if primary - 1 < len(row) else ""
                merged.append(primary_val or STATUS_NOT_SOLVED)
        if merged:
            col_letter = _col_letter(primary)
            ws.update(f"{col_letter}{DATA_START_ROW}:{col_letter}{DATA_START_ROW + len(merged) - 1}",
                      [[v] for v in merged], value_input_option="USER_ENTERED")
        cols_to_delete.extend(c for c in cols if c != primary)

    for c in sorted(set(cols_to_delete), reverse=True):  # rightmost first — keeps earlier indices valid
        ws.delete_columns(c)

    return len(cols_to_delete)


def _find_or_create_problem_column(ws, problem_name, problem_url, due_date=None):
    headers = _header_row_values(ws)
    formulas = _header_row_formulas(ws)
    target_key = _norm_problem_key(problem_name, problem_url)

    for i, h in enumerate(headers, start=1):
        f = formulas[i - 1] if i - 1 < len(formulas) else ""
        if target_key and _header_col_key(f, h) == target_key:
            return i

    # No existing column — append a new one right after the last used column.
    new_col = len(headers) + 1 if headers else 1
    header_text = problem_name or problem_url or "Problem"
    if due_date:
        header_text = f"{header_text} (Due {due_date})"

    if problem_url:
        header_formula = f'=HYPERLINK("{problem_url}","{header_text}")'
        ws.update_cell(HEADER_ROW, new_col, header_formula)
    else:
        ws.update_cell(HEADER_ROW, new_col, header_text)

    # Dropdown validation (Solved / Not Solved) for the whole data range in
    # this new column, matching the style already used in your sheet.
    col_letter = _col_letter(new_col)
    data_range = f"{col_letter}{DATA_START_ROW}:{col_letter}{ws.row_count}"
    try:
        ws.add_validation(
            data_range,
            "ONE_OF_LIST",
            [STATUS_SOLVED, STATUS_NOT_SOLVED],
            showCustomUi=True,
        )
    except Exception as e:
        # Validation is a nice-to-have; don't let it block the actual write.
        print(f"[mentor_sheet_sync] dropdown validation failed: {e}")

    try:
        _apply_conditional_colors(ws, new_col)
    except Exception as e:
        print(f"[mentor_sheet_sync] conditional formatting failed: {e}")

    return new_col


def _apply_conditional_colors(ws, col_idx):
    """Green for 'Solved', red for 'Not Solved' — matches the screenshot."""
    sheet_id = ws.id
    col_letter = _col_letter(col_idx)
    rng = {
        "sheetId": sheet_id,
        "startRowIndex": DATA_START_ROW - 1,
        "endRowIndex": ws.row_count,
        "startColumnIndex": col_idx - 1,
        "endColumnIndex": col_idx,
    }
    requests_body = {
        "requests": [
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [rng],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": STATUS_SOLVED}]},
                            "format": {"backgroundColor": {"red": 0.71, "green": 0.88, "blue": 0.71}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [rng],
                        "booleanRule": {
                            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": STATUS_NOT_SOLVED}]},
                            "format": {"backgroundColor": {"red": 0.96, "green": 0.71, "blue": 0.71}},
                        },
                    },
                    "index": 0,
                }
            },
        ]
    }
    ws.spreadsheet.batch_update(requests_body)


def remove_regn_num_column(get_db=None):
    """
    One-off migration for sheets created before the Regn Num column was
    dropped from ROSTER_BASE_HEADERS: finds a "Regn Num" (or "Register
    Number" / "Regn. Num" etc.) header still sitting in the live sheet
    and deletes that whole column. Safe to call repeatedly — a no-op once
    the column is gone. Everything else (problem columns, Reg No
    matching) is found by header text, not position, so this doesn't
    disturb anything to the right of the deleted column.
    """
    with _lock:
        try:
            ws = get_or_create_roster_ws(get_db)
            headers = _header_row_values(ws)
            for i, h in enumerate(headers, start=1):
                # Only ever matches "Regn Num" / "Regn. Num" / "Register Num(ber)" —
                # deliberately does NOT match "Reg No", which stays as the key column.
                if re.sub(r"[^a-z]", "", (h or "").lower()) in ("regnnum", "registernum", "registernumber"):
                    ws.delete_columns(i)
                    return {"success": True, "removed": True, "column": h}
            return {"success": True, "removed": False}
        except Exception as e:
            print(f"[mentor_sheet_sync] remove_regn_num_column failed: {e}")
            return {"success": False, "error": str(e)}


def sync_assignment_to_sheet(problem_name, problem_url, due_date, students, get_db=None):
    """
    students: list of dicts, each with reg_no, full_name, completed (bool).
    get_db: optional — passed through so a first-ever assignment can
    create the roster tab and immediately fill it with every student's
    details (see get_or_create_roster_ws()). If omitted and the tab
    doesn't exist yet, it's still created, just left to fill in via the
    next resync_all() instead.
    Best-effort: swallow Sheets errors so a Sheets outage never breaks the
    assign flow itself (the DB write already succeeded by the time this
    runs — this is a mirror, not the source of truth).
    """
    with _lock:
        try:
            ws = get_or_create_roster_ws(get_db)
            repair_duplicate_problem_columns(ws)

            headers = _header_row_values(ws)
            row_index, key_col, mode = _build_row_index(ws, headers)
            if not row_index:
                print("[mentor_sheet_sync] could not find a Reg No / Name column — skipped sheet sync.")
                return False

            col = _find_or_create_problem_column(ws, problem_name, problem_url, due_date)

            updates = []
            for s in students:
                row = row_index.get(s.get("reg_no")) or row_index.get((s.get("full_name") or "").lower())
                if not row:
                    continue  # student not on the roster yet — resync_all() will add them
                value = STATUS_SOLVED if s.get("completed") else STATUS_NOT_SOLVED
                updates.append({"range": f"{_col_letter(col)}{row}", "values": [[value]]})

            if updates:
                ws.batch_update(updates)
            return True
        except Exception as e:
            print(f"[mentor_sheet_sync] sync_assignment_to_sheet failed: {e}")
            return False


def resync_all(get_db):
    """
    Full resync: for every mentor_assignment in the DB, make sure its
    column exists and every cell reflects current `completed` status.
    Also appends a roster row for any active, non-admin student missing
    from the sheet (new-user support).
    Returns a summary dict for the caller to show in the UI.
    """
    with _lock:
        summary = {"columns_touched": 0, "cells_updated": 0, "rows_added": 0, "skipped": False}
        try:
            ws = get_or_create_roster_ws(get_db)
            summary["duplicate_columns_merged"] = repair_duplicate_problem_columns(ws)

            headers = _header_row_values(ws)
            row_index, key_col, mode = _build_row_index(ws, headers)

            with get_db() as db:
                # 1) Add missing students as new rows.
                students = db.execute(
                    "SELECT id, username, full_name, reg_no, branch FROM users "
                    "WHERE status='active' AND is_admin=0"
                ).fetchall()
                for s in students:
                    key = s["reg_no"] if mode == "reg_no" else (s["full_name"] or "").lower()
                    if key and key not in row_index:
                        new_row_num = ws.row_count if ws.row_count else DATA_START_ROW
                        ws.append_row(
                            [s["reg_no"] or "", s["full_name"] or s["username"], s["branch"] or ""],
                            table_range=f"A{DATA_START_ROW}",
                        )
                        summary["rows_added"] += 1
                # Re-read the row index after any appends.
                if summary["rows_added"]:
                    row_index, key_col, mode = _build_row_index(ws, _header_row_values(ws))

                # 2) Walk every distinct assigned problem and sync its column.
                problems = db.execute("""
                    SELECT DISTINCT problem_name, problem_url, due_date
                    FROM mentor_assignments
                """).fetchall()

                for p in problems:
                    col = _find_or_create_problem_column(ws, p["problem_name"], p["problem_url"], p["due_date"])
                    summary["columns_touched"] += 1

                    rows = db.execute("""
                        SELECT ma.completed, u.reg_no, u.full_name
                        FROM mentor_assignments ma
                        JOIN users u ON u.id = ma.user_id
                        WHERE (ma.problem_url=%s AND %s <> '')
                           OR (%s = '' AND LOWER(ma.problem_name)=LOWER(%s))
                    """, (p["problem_url"], p["problem_url"] or "", p["problem_url"] or "", p["problem_name"])
                    ).fetchall()

                    updates = []
                    for r in rows:
                        row = row_index.get(r["reg_no"]) or row_index.get((r["full_name"] or "").lower())
                        if not row:
                            continue
                        value = STATUS_SOLVED if r["completed"] else STATUS_NOT_SOLVED
                        updates.append({"range": f"{_col_letter(col)}{row}", "values": [[value]]})
                    if updates:
                        ws.batch_update(updates)
                        summary["cells_updated"] += len(updates)

            return summary
        except Exception as e:
            print(f"[mentor_sheet_sync] resync_all failed: {e}")
            summary["skipped"] = True
            summary["error"] = str(e)
            return summary
