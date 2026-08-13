"""
contest/contest_sheet.py — the per-year Student_Contest Google Sheet tab.

PHASE 2 FIX: this used to hardcode ONE shared spreadsheet (SHEET_ID) for
every student's contest tracking, regardless of year. That's gone —
services/year_sheet_service.py is now the ONLY year -> spreadsheet
resolver, same as normal_sync.py / mentor_sheet_sync.py / bot_sheet_sync.py.
Every function here takes an explicit `year`, resolved by the caller:
  - routes/contest.py (mentor/admin operations): year comes from the
    request, validated against services.year_sheet_service.is_year_configured()
    before it ever reaches this module.
  - contest_sync.py (automated grading): year comes from the contest's
    OWN stored cohort_year column (contest_events.cohort_year) — never
    re-derived from a request, never guessed.
There is no fallback to a shared/default spreadsheet anywhere below —
if `year` doesn't resolve to a configured sheet,
services.year_sheet_service.open_year_spreadsheet() raises, and every
function here surfaces that as a clear (False, message) result rather
than silently writing somewhere else.

IMPORTANT — this file could not be tested against the live Google Sheets
API in the environment it was written in (no network access to
googleapis.com there). It follows the exact same
services.year_sheet_service auth/resolution pattern as
normal_sync.py's get_sheet(), which IS known-working in your deployment,
and the column/row logic below is unit-tested against an in-memory fake
in contest/tests/test_contest_sheet_logic.py. But you should still do
one real end-to-end test (create a contest, check the actual sheet)
before relying on this in front of students.

Sheet layout (fixed, per the Contest Tracker design doc), inside THAT
YEAR's spreadsheet:

    Reg No | Roll No | Name | Branch | <contest_code_1> | <contest_code_2> | ... | Total Solved | Contests Attended | Attendance % | _user_id

`_user_id` is NOT part of the design doc's visible table — it's appended
as the last column purely so the backend can reliably match sheet rows
back to DB users even before every student has a Reg No filled in (reg_no
is blank for un-backfilled students, so it can't be the matching key on
its own — see the migration 0003 docstring and the conversation this was
built from). Admins can ignore that column; nothing in the UI surfaces it.
"""

import gspread

from database.db import get_db
from services.year_sheet_service import open_year_spreadsheet

STUDENT_CONTEST_TAB = "Student_Contest"

BASE_COLUMNS = ["Reg No", "Roll No", "Name", "Branch"]
SUMMARY_COLUMNS = ["Total Solved", "Contests Attended", "Attendance %"]
KEY_COLUMN = "_user_id"
ABSENT_MARKER = "ABS"


def _get_spreadsheet(year):
    """Resolves `year` to that year's spreadsheet via
    services.year_sheet_service — raises ValueError if the year has no
    sheet configured yet (no fallback to any shared/default sheet)."""
    return open_year_spreadsheet(year)


# ── Sheet creation (once per year, ever) ───────────────────────────────────
def get_or_create_student_contest_sheet(year):
    spreadsheet = _get_spreadsheet(year)
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
def import_students(sheet, year):
    """Adds any active, non-admin student IN THIS YEAR ONLY who isn't
    already present on the sheet (matched by the hidden _user_id
    column). Returns the number of rows added."""
    header = _header(sheet)
    all_values = sheet.get_all_values()
    uid_col = _col_index(header, KEY_COLUMN) - 1  # 0-based for list indexing
    existing_uids = {row[uid_col] for row in all_values[1:] if len(row) > uid_col}

    with get_db() as db:
        students = db.execute("""
            SELECT id, username, full_name, reg_no, roll_no, branch
            FROM users
            WHERE status='active' AND is_admin=0 AND cohort_year=?
            ORDER BY username ASC
        """, (str(year),)).fetchall()

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
    already exists — this makes re-running contest creation safe.

    BUG FIXED HERE: gspread's insert_cols(values, col) treats the OUTER
    list as "one entry per column to insert", with each INNER list being
    that column's values top-to-bottom. The previous version built
    [[contest_code], [ABS], [ABS], ...] — a list of N+1 separate one-row
    columns — instead of [[contest_code, ABS, ABS, ...]], ONE column with
    N+1 rows. That inserted a wall of stray single-cell "ABS" columns
    across row 1 (visible in the live sheet) instead of one abc466 column
    running down every student's row.
    """
    header = _header(sheet)
    if contest_code in header:
        return False

    total_solved_idx = _col_index(header, "Total Solved")  # 1-based; insert goes right before it
    all_values = sheet.get_all_values()
    num_data_rows = max(len(all_values) - 1, 0)

    column_values = [[contest_code] + [ABSENT_MARKER] * num_data_rows]  # ONE column, N+1 rows
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
            cell = (cell or "").strip()
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
    contest: dict with at least 'contest_code' and 'cohort_year' (the
    contest's OWN stored year — see contest_events.cohort_year).
    Runs the full flow for one newly created contest: get/create THAT
    YEAR's sheet -> import any new students in that year -> add the
    contest column -> recalculate summary columns.

    Returns (ok: bool, message: str). Never raises — callers (routes)
    treat a Sheets failure as non-fatal: the contest still exists in
    Postgres either way, same as how email failures don't block signup
    elsewhere in this app.
    """
    year = (contest.get("cohort_year") or "").strip() if contest.get("cohort_year") else None
    if not year:
        return False, "This contest has no year/cohort set — cannot resolve a spreadsheet."
    try:
        sheet = get_or_create_student_contest_sheet(year)
        repair_stray_columns(sheet)
        added = import_students(sheet, year)
        created_col = add_contest_column(sheet, contest["contest_code"])
        if created_col:
            recalculate_summary(sheet)
        msg = f"Sheet synced ({added} student(s) imported"
        msg += ", contest column added)." if created_col else ", contest column already existed)."
        return True, msg
    except Exception as e:
        return False, f"Google Sheet sync failed: {e}"


def write_contest_results(contest_code, results_by_user_id, year):
    """Writes each participant's solved count into the contest's column
    (matched by the hidden _user_id column) IN THAT CONTEST'S YEAR sheet,
    then recalculates the summary columns. Anyone in results_by_user_id
    who didn't solve anything (participated=False) is written as ABS,
    same marker as a student who was never touched at all — there's no
    separate "attempted, solved zero" signal in this design (see
    contest_sync.py's module docstring).

    results_by_user_id: {user_id (int): {"solved": int, "participated": bool, ...}}
    year: the contest's OWN stored cohort_year — never re-derived from a
    request. A contest created for 2028 can only ever write results to
    the 2028 spreadsheet, regardless of who triggers the sync.

    Returns (ok: bool, message: str). Never raises — same non-fatal
    contract as ensure_sheet_for_contest()/refresh_sheet() above.
    """
    if not year:
        return False, "This contest has no year/cohort set — cannot resolve a spreadsheet."
    try:
        sheet = get_or_create_student_contest_sheet(year)
        header = _header(sheet)
        col_idx = _col_index(header, contest_code)
        if not col_idx:
            return False, (f"Contest column '{contest_code}' not found on the sheet — "
                            f"the contest column gets created when the contest is made; "
                            f"try 'Refresh Sheet' first.")

        all_values = sheet.get_all_values()
        uid_col = _col_index(header, KEY_COLUMN) - 1  # 0-based

        cells = []
        for row_num, row in enumerate(all_values[1:], start=2):
            if len(row) <= uid_col:
                continue
            uid_raw = row[uid_col]
            if not uid_raw or not uid_raw.isdigit():
                continue
            uid = int(uid_raw)
            if uid not in results_by_user_id:
                continue
            r = results_by_user_id[uid]
            value = r["solved"] if r.get("participated") else ABSENT_MARKER
            cells.append(gspread.Cell(row_num, col_idx, value))

        if cells:
            sheet.update_cells(cells, value_input_option="USER_ENTERED")

        recalculate_summary(sheet)
        return True, f"{len(cells)} result(s) written to the sheet."
    except Exception as e:
        return False, f"Google Sheet result write failed: {e}"


def repair_stray_columns(sheet):
    """One-time cleanup for the insert_cols() bug fixed above in
    add_contest_column(): every junk column it created is headed literally
    'ABS' (the marker itself — a real contest column is always headed by
    a contest_code like 'abc466', never by ABSENT_MARKER), and holds a
    value only in row 1 since each was its own one-row column. Detects and
    deletes every such column in the contest-code range. Safe against ever
    touching real data, since a genuine contest column can never be named
    exactly 'ABS'. Returns the number of columns removed."""
    header = _header(sheet)
    start = len(BASE_COLUMNS)
    end = header.index("Total Solved") if "Total Solved" in header else len(header)
    junk_positions = [i for i in range(start, end) if header[i] == ABSENT_MARKER]
    if not junk_positions:
        return 0
    # Delete rightmost-first so earlier indices in the list stay valid as
    # columns shift left after each deletion.
    for idx in sorted(junk_positions, reverse=True):
        sheet.delete_columns(idx + 1)  # gspread column indices are 1-based
    return len(junk_positions)


def refresh_sheet(year):
    """Re-import students (in THAT YEAR), clean up any stray junk columns
    from the insert_cols() bug, and recalculate summary columns — used by
    an admin's 'Refresh Sheet' action for one specific year."""
    if not year:
        return False, "Select a year first."
    try:
        sheet = get_or_create_student_contest_sheet(year)
        removed = repair_stray_columns(sheet)
        added = import_students(sheet, year)
        recalculate_summary(sheet)
        msg = f"Sheet refreshed ({added} new student(s) imported"
        if removed:
            msg += f", {removed} stray column(s) from an earlier bug removed"
        msg += ")."
        return True, msg
    except Exception as e:
        return False, f"Google Sheet refresh failed: {e}"
