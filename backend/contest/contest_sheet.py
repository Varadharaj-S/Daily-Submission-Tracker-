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

Sheet layout — TWO header rows (row 1 = contest date, row 2 = everything
else), matching the reference sheet this was redesigned from:

    Row 1:  |      |         |      |        | <date_1> | <date_2> | ... |      |      |      |
    Row 2:  | Reg No | Roll No | Name | Branch | <code_1> | <code_2> | ... | Total Solved | Contests Attended | Attendance % | _user_id

Data rows start at row 3.

`_user_id` is NOT part of the design doc's visible table — it's appended
as the last column purely so the backend can reliably match sheet rows
back to DB users even before every student has a Reg No filled in (reg_no
is blank for un-backfilled students, so it can't be the matching key on
its own — see the migration 0003 docstring and the conversation this was
built from). Admins can ignore that column; nothing in the UI surfaces it.

Cell format for a contest column: "<in_window>" or "<in_window>(<after_window>)"
— e.g. "2" (solved 2 during the contest window), "0(3)" (solved 0 during
the window, 3 more later the same day), or blank (solved nothing, in or
out of the window — blank and "0" mean the same thing here, so we just
leave it blank for a cleaner-looking sheet).
"""

import os
import json
import re

import gspread
from google.oauth2.service_account import Credentials

from database.db import get_db

SHEET_ID = "1vucuD_-SCKFDJYC-XWNRPGJkXqu_3CyOVDTnFRezusE"
CREDENTIALS_FILE = "valiant-splicer-489013-q2-40d3ac23a2d8.json"
STUDENT_CONTEST_TAB = "Student_Contest"

BASE_COLUMNS = ["Reg No", "Roll No", "Name", "Branch"]
SUMMARY_COLUMNS = ["Total Solved", "Contests Attended", "Attendance %"]
KEY_COLUMN = "_user_id"

CODE_HEADER_ROW = 2   # row with Reg No / contest codes / _user_id
DATE_HEADER_ROW = 1   # row with contest dates (above the code row)
FIRST_DATA_ROW  = 3


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
        code_header = BASE_COLUMNS + SUMMARY_COLUMNS + [KEY_COLUMN]
        date_header = [""] * len(code_header)  # no contest columns yet
        sheet.append_row(date_header)
        sheet.append_row(code_header)
    return sheet


# ── Header helpers ─────────────────────────────────────────────────────────────
def _header(sheet):
    """The row 2 header — Reg No / contest codes / summary cols / _user_id."""
    return sheet.row_values(CODE_HEADER_ROW)


def _date_header(sheet):
    """The row 1 header — contest dates, aligned with row 2's contest columns."""
    return sheet.row_values(DATE_HEADER_ROW)


def _contest_columns(header):
    """Contest code columns are everything between the base columns and
    'Total Solved'."""
    start = len(BASE_COLUMNS)
    end = header.index("Total Solved")
    return header[start:end]


def _col_index(header, name):
    """1-based column index, or None."""
    return header.index(name) + 1 if name in header else None


def _format_date_label(d):
    """'11 April 2026' style label for the date header row. `d` is a
    datetime.date (or None if unknown)."""
    if d is None:
        return ""
    return f"{d.day} {d.strftime('%B')} {d.year}"


# ── Student import (additive only — never deletes, never duplicates) ─────────
def import_students(sheet):
    """Adds any active, non-admin student not already present on the sheet
    (matched by the hidden _user_id column). Returns the number of rows
    added."""
    header = _header(sheet)
    all_values = sheet.get_all_values()
    uid_col = _col_index(header, KEY_COLUMN) - 1  # 0-based for list indexing
    existing_uids = {row[uid_col] for row in all_values[FIRST_DATA_ROW - 1:] if len(row) > uid_col}

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
        row += [""] * len(contest_cols)               # blank — hasn't participated in any past contest
        row += [0, 0, "0%"]                            # Total Solved / Attended / Attendance %
        row += [str(s["id"])]                          # _user_id
        new_rows.append(row)

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows)


# ── Contest column (additive only — never duplicates) ─────────────────────────
def add_contest_column(sheet, contest_code, contest_date=None):
    """Inserts a new contest column, positioned right before 'Total Solved'
    so the three summary columns always stay rightmost. Every existing
    student row starts blank in the new column (blank = 0 = hasn't
    participated yet). No-op (returns False) if the column already exists
    — this makes re-running contest creation safe."""
    header = _header(sheet)
    if contest_code in header:
        return False

    total_solved_idx = _col_index(header, "Total Solved")  # 1-based; insert goes right before it
    all_values = sheet.get_all_values()
    num_data_rows = max(len(all_values) - (FIRST_DATA_ROW - 1), 0)

    date_label = _format_date_label(contest_date)
    column_values = [[date_label], [contest_code]] + [[""] for _ in range(num_data_rows)]
    sheet.insert_cols(column_values, total_solved_idx)
    return True


# ── Summary recalculation ──────────────────────────────────────────────────────
def recalculate_summary(sheet):
    """Recomputes Total Solved / Contests Attended / Attendance % for every
    row from the contest columns, and writes all three in a single batch
    update (not one API call per cell — this sheet can have 100+ students
    and grow a column per contest, so batching matters).

    A cell counts as 'attended' if it's non-blank (any digits at all —
    including "0(2)", solved-late-only). 'Total Solved' only counts the
    in-window number (the part before any parenthesis) — after-window
    solves don't count toward the official total, same as they don't
    count toward contest rank."""
    header = _header(sheet)
    contest_cols = _contest_columns(header)
    if not contest_cols:
        return

    start_col = len(BASE_COLUMNS) + 1     # 1-based, first contest column
    end_col = start_col + len(contest_cols) - 1
    total_idx = _col_index(header, "Total Solved")

    all_values = sheet.get_all_values()
    data_rows = all_values[FIRST_DATA_ROW - 1:]

    summary_rows = []
    for row in data_rows:
        contest_cells = row[start_col - 1:end_col] if len(row) >= end_col else \
            (row[start_col - 1:] + [""] * (end_col - len(row)))
        total_solved = 0
        attended = 0
        for cell in contest_cells:
            cell = (cell or "").strip()
            if not cell:
                continue
            attended += 1
            m = re.match(r"(\d+)", cell)
            if m:
                total_solved += int(m.group(1))
        attendance_pct = round((attended / len(contest_cols)) * 100) if contest_cols else 0
        summary_rows.append([total_solved, attended, f"{attendance_pct}%"])

    if summary_rows:
        col_letter_start = gspread.utils.rowcol_to_a1(2, total_idx).rstrip("0123456789")
        col_letter_end = gspread.utils.rowcol_to_a1(2, total_idx + 2).rstrip("0123456789")
        cell_range = f"{col_letter_start}{FIRST_DATA_ROW}:{col_letter_end}{FIRST_DATA_ROW - 1 + len(summary_rows)}"
        sheet.update(cell_range, summary_rows, value_input_option="USER_ENTERED")


# ── Orchestration — called from routes/contest.py on contest creation ────────
def ensure_sheet_for_contest(contest):
    """
    contest: dict with at least 'contest_code' (and ideally 'contest_date'
    — used for the date header row when the column is first created).
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
        created_col = add_contest_column(sheet, contest["contest_code"], contest.get("contest_date"))
        if created_col:
            recalculate_summary(sheet)
        msg = f"Sheet synced ({added} student(s) imported"
        msg += ", contest column added)." if created_col else ", contest column already existed)."
        return True, msg
    except Exception as e:
        return False, f"Google Sheet sync failed: {e}"


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


# ── write_contest_results — called from contest_sync.py after grading ────────

def _format_result_cell(solved, after_window):
    """'<solved>' or '<solved>(<after_window>)', blank if both are 0
    (blank and 0 mean the same thing here — a cleaner-looking sheet)."""
    if solved == 0 and after_window == 0:
        return ""
    if after_window > 0:
        return f"{solved}({after_window})"
    return str(solved)


def write_contest_results(contest_code, contest_date, results_by_user_id):
    """
    Writes graded results for one contest into the Student_Contest sheet.

    contest_date: date object for the contest — only used if the contest
      column doesn't exist yet and needs to be created (sets the date
      header row). Safe to pass None if unknown; the date cell is just
      left blank in that case.

    results_by_user_id: {user_id (int): {"solved": int, "after_window": int,
                                          "participated": bool, ...}}

    For each student row (matched via the hidden _user_id column), writes
    "<solved>" / "<solved>(<after_window>)" / blank per _format_result_cell.

    After writing all cells, recalculates the summary columns (Total Solved,
    Contests Attended, Attendance %) in one batch call.

    Returns (ok: bool, message: str). Never raises.
    """
    try:
        sheet = get_or_create_student_contest_sheet()
        header = _header(sheet)

        # Ensure the contest column exists
        if contest_code not in header:
            add_contest_column(sheet, contest_code, contest_date)
            header = _header(sheet)  # re-read after insert

        contest_col_idx = _col_index(header, contest_code)  # 1-based
        uid_col_idx     = _col_index(header, KEY_COLUMN) - 1  # 0-based

        all_values = sheet.get_all_values()
        data_rows  = all_values[FIRST_DATA_ROW - 1:]  # skip both header rows

        updates = []  # [(row_number_1based, col_1based, value)]
        for i, row in enumerate(data_rows):
            sheet_row = i + FIRST_DATA_ROW
            uid = row[uid_col_idx].strip() if len(row) > uid_col_idx else ""
            if not uid:
                continue

            try:
                uid_int = int(uid)
            except ValueError:
                continue

            result = results_by_user_id.get(uid_int)
            if result is None:
                # Student not in results (wasn't a participant for this platform)
                continue

            cell_val = _format_result_cell(result.get("solved", 0), result.get("after_window", 0))
            updates.append((sheet_row, contest_col_idx, cell_val))

        # Batch update: one API call per cell is too slow for 50+ students.
        # gspread's batch_update / update with a range is the right call here.
        if updates:
            cell_list = []
            for (r, c, v) in updates:
                cell = gspread.utils.rowcol_to_a1(r, c)
                cell_list.append({"range": cell, "values": [[v]]})
            sheet.batch_update(cell_list, value_input_option="USER_ENTERED")

        recalculate_summary(sheet)
        return True, f"Sheet updated: {len(updates)} student(s) written for '{contest_code}'."

    except Exception as e:
        return False, f"Sheet write failed for '{contest_code}': {e}"