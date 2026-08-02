"""
contest/contest_sheet.py — Phase 2+3: the single Student_Contest Google
Sheet. Creates it once, never again; only ever adds rows (new students)
and columns (new contests), never deletes or duplicates either.

IMPORTANT — this file could not be tested against the live Google Sheets
API in the environment it was written in (no network access to
googleapis.com there). It follows the exact same gspread + service-account
auth pattern as normal_sync.py's get_sheet(), which IS known-working in
your deployment, and the column/row logic below is unit-tested against an
in-memory fake in contest/tests/test_contest_sheet_logic.py (see that file
— it exercises the real functions here, just swaps out the gspread client
for a fake one that behaves like a real spreadsheet). But you should still
do one real end-to-end test (create a contest, check the actual sheet)
before relying on this in front of students.

Sheet layout (fixed, per the Contest Tracker design doc):

    Reg No | Roll No | Name | Branch | <contest_code_1> | <contest_code_2> | ... | Total Solved | Contests Attended | Attendance % | _user_id

`_user_id` is NOT part of the design doc's visible table — it's appended
as the last column purely so the backend can reliably match sheet rows
back to DB users even before every student has a Reg No filled in (reg_no
is blank for un-backfilled students, so it can't be the matching key on
its own — see the migration 0003 docstring and the conversation this was
built from). Admins can ignore that column; nothing in the UI surfaces it.
"""

import os
import json

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_db

SHEET_ID = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"
CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"
STUDENT_CONTEST_TAB = "Student_Contest"

BASE_COLUMNS = ["Reg No", "Roll No", "Name", "Branch"]
SUMMARY_COLUMNS = ["Total Solved", "Contests Attended", "Attendance %"]
KEY_COLUMN = "_user_id"
ABSENT_MARKER = "ABS"


# ── Auth (same pattern as normal_sync.py's get_sheet) ─────────────────────────
def _get_client():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.getenv("GOOGLE_SERVICE_JSON"):
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(service_info, scopes=scope)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
    return gspread.authorize(creds)


def _get_spreadsheet(client=None):
    client = client or _get_client()
    return client.open_by_key(SHEET_ID)


# ── Sheet creation (once, ever) ────────────────────────────────────────────────
def get_or_create_student_contest_sheet(client=None):
    spreadsheet = _get_spreadsheet(client)
    try:
        sheet = spreadsheet.worksheet(STUDENT_CONTEST_TAB)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=STUDENT_CONTEST_TAB, rows="2000", cols="20")
        header = BASE_COLUMNS + SUMMARY_COLUMNS + [KEY_COLUMN]
        sheet.append_row(header)
    return sheet


# ── Header helpers ─────────────────────────────────────────────────────────────
def _header(sheet):
    return sheet.row_values(1)


def _contest_columns(header):
    """Contest code columns are everything between the base columns and
    'Total Solved'."""
    start = len(BASE_COLUMNS)
    end = header.index("Total Solved")
    return header[start:end]


def _col_index(header, name):
    """1-based column index, or None."""
    return header.index(name) + 1 if name in header else None


# ── Student import (additive only — never deletes, never duplicates) ─────────
def import_students(sheet):
    """Adds any active, non-admin student not already present on the sheet
    (matched by the hidden _user_id column). Returns the number of rows
    added."""
    header = _header(sheet)
    all_values = sheet.get_all_values()
    uid_col = _col_index(header, KEY_COLUMN) - 1  # 0-based for list indexing
    existing_uids = {row[uid_col] for row in all_values[1:] if len(row) > uid_col}

    with get_db() as db:
        students = db.execute("""
            SELECT id, username, full_name, reg_no, roll_no, branch
            FROM users
            WHERE status='active' AND is_admin=0
            ORDER BY username ASC
        """).fetchall()

    contest_cols = _contest_columns(header)
    new_rows = []
    for s in students:
        if str(s["id"]) in existing_uids:
            continue
        row = [
            s["reg_no"] or "",
            s["roll_no"] or "",
            s["full_name"] or s["username"],
            s["branch"] or "",
        ]
        row += [ABSENT_MARKER] * len(contest_cols)  # hasn't participated in any past contest
        row += [0, 0, "0%"]                          # Total Solved / Attended / Attendance %
        row += [str(s["id"])]                         # _user_id
        new_rows.append(row)

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows)


# ── Contest column (additive only — never duplicates) ─────────────────────────
def add_contest_column(sheet, contest_code):
    """Inserts a new contest column, positioned right before 'Total Solved'
    so the three summary columns always stay rightmost. Every existing
    student row is initialized to ABS in the new column (design doc:
    'Initialize all students as ABS'). No-op (returns False) if the column
    already exists — this makes re-running contest creation safe."""
    header = _header(sheet)
    if contest_code in header:
        return False

    total_solved_idx = _col_index(header, "Total Solved")  # 1-based; insert goes right before it
    all_values = sheet.get_all_values()
    num_data_rows = max(len(all_values) - 1, 0)

    column_values = [[contest_code]] + [[ABSENT_MARKER] for _ in range(num_data_rows)]
    sheet.insert_cols(column_values, total_solved_idx)
    return True


# ── Summary recalculation ──────────────────────────────────────────────────────
def recalculate_summary(sheet):
    """Recomputes Total Solved / Contests Attended / Attendance % for every
    row from the contest columns, and writes all three in a single batch
    update (not one API call per cell — this sheet can have 100+ students
    and grow a column per contest, so batching matters)."""
    header = _header(sheet)
    contest_cols = _contest_columns(header)
    if not contest_cols:
        return

    start_col = len(BASE_COLUMNS) + 1     # 1-based, first contest column
    end_col = start_col + len(contest_cols) - 1
    total_idx = _col_index(header, "Total Solved")

    all_values = sheet.get_all_values()
    data_rows = all_values[1:]

    summary_rows = []
    for row in data_rows:
        contest_cells = row[start_col - 1:end_col] if len(row) >= end_col else \
            (row[start_col - 1:] + [""] * (end_col - len(row)))
        total_solved = 0
        attended = 0
        for cell in contest_cells:
            cell = str(cell if cell is not None else "").strip()
            if cell and cell != ABSENT_MARKER:
                attended += 1
                if cell.lstrip("-").isdigit():
                    total_solved += int(cell)
        attendance_pct = round((attended / len(contest_cols)) * 100) if contest_cols else 0
        summary_rows.append([total_solved, attended, f"{attendance_pct}%"])

    if summary_rows:
        col_letter_start = gspread.utils.rowcol_to_a1(2, total_idx).rstrip("0123456789")
        col_letter_end = gspread.utils.rowcol_to_a1(2, total_idx + 2).rstrip("0123456789")
        cell_range = f"{col_letter_start}2:{col_letter_end}{1 + len(summary_rows)}"
        sheet.update(cell_range, summary_rows, value_input_option="USER_ENTERED")


# ── Orchestration — called from routes/contest.py on contest creation ────────
def ensure_sheet_for_contest(contest):
    """
    contest: dict with at least 'contest_code'.
    Runs the full Phase 2+3 flow for one newly created contest:
    get/create sheet -> import any new students -> add the contest column
    -> recalculate summary columns.

    Returns (ok: bool, message: str). Never raises — callers (routes) treat
    a Sheets failure as non-fatal: the contest still exists in Postgres
    either way, same as how email failures don't block signup elsewhere
    in this app.
    """
    try:
        sheet = get_or_create_student_contest_sheet()
        added = import_students(sheet)
        created_col = add_contest_column(sheet, contest["contest_code"])
        if created_col:
            recalculate_summary(sheet)
        msg = f"Sheet synced ({added} student(s) imported"
        msg += ", contest column added)." if created_col else ", contest column already existed)."
        return True, msg
    except Exception as e:
        return False, f"Google Sheet sync failed: {e}"


def write_contest_results(contest_code, results_by_user_id):
    """
    Writes each participant's solved count into their row's contest_code
    column, then recalculates the summary columns. Called by
    contest_sync.sync_one_contest() after grading is done in Postgres —
    this is the step that actually gets numbers onto the Student_Contest
    sheet instead of everyone staying on the default "ABS".

    contest_code: str, must already be a column header (ensure_sheet_for_contest
        creates it at contest-creation time; this also creates it as a
        fallback if that step somehow didn't run, so a sync never silently
        no-ops just because the column is missing).
    results_by_user_id: {user_id(int): {"solved": int, "participated": bool,
        "rank": int|None, "score": int}} — as built by contest_sync.py.
        Students not in this dict, or with participated=False, are left as
        whatever's already in their cell (ABS by default) — a 0-solve
        result and "never attempted" both mean nothing was submitted, so
        there's no scored value to write.

    Returns (ok: bool, message: str). Never raises.
    """
    try:
        sheet = get_or_create_student_contest_sheet()
        header = _header(sheet)
        if contest_code not in header:
            add_contest_column(sheet, contest_code)
            header = _header(sheet)

        col_idx = _col_index(header, contest_code)
        uid_col = _col_index(header, KEY_COLUMN)
        all_values = sheet.get_all_values()
        data_rows = all_values[1:]

        # Build the full column top-to-bottom and write it in one call —
        # same pattern recalculate_summary() below already uses, and it's
        # the one both the real gspread Worksheet and the FakeWorksheet
        # test double actually implement (unlike batch_update, which the
        # test double doesn't have).
        col_values = []
        written = 0
        for row in data_rows:
            uid_cell = row[uid_col - 1] if len(row) >= uid_col else ""
            existing_cell = row[col_idx - 1] if len(row) >= col_idx else ABSENT_MARKER
            uid = None
            if uid_cell:
                try:
                    uid = int(uid_cell)
                except ValueError:
                    uid = None
            result = results_by_user_id.get(uid) if uid is not None else None
            if result and result.get("participated"):
                # Write as a string, not an int — recalculate_summary()
                # below calls .strip() on every cell it reads back, which
                # blows up on a raw int (caught via the FakeWorksheet smoke
                # test; real Sheets reads back FORMATTED_VALUE strings
                # anyway, so this matches what it'll actually see there).
                col_values.append([str(result.get("solved", 0))])
                written += 1
            else:
                # Not a participant in this sync (or no matching uid) —
                # leave the cell exactly as it was (ABS by default).
                col_values.append([existing_cell])

        if col_values:
            col_letter = gspread.utils.rowcol_to_a1(1, col_idx).rstrip("0123456789")
            cell_range = f"{col_letter}2:{col_letter}{1 + len(col_values)}"
            sheet.update(cell_range, col_values, value_input_option="USER_ENTERED")

        recalculate_summary(sheet)
        return True, f"{written} result(s) written to sheet."
    except Exception as e:
        return False, f"Google Sheet write failed: {e}"


def refresh_sheet():
    """Re-import students and recalculate summary columns without adding a
    contest column — used by an admin 'Refresh Sheet' action."""
    try:
        sheet = get_or_create_student_contest_sheet()
        added = import_students(sheet)
        recalculate_summary(sheet)
        return True, f"Sheet refreshed ({added} new student(s) imported)."
    except Exception as e:
        return False, f"Google Sheet refresh failed: {e}"
