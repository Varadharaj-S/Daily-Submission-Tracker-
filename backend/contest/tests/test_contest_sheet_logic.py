"""
contest/tests/test_contest_sheet_logic.py — exercises the real
add_contest_column / import_students / recalculate_summary functions from
contest_sheet.py against a FakeWorksheet (see fake_worksheet.py) instead
of a live Google Sheet, plus a real Postgres DB for the student-import
query (set DATABASE_URL before running).

Run:
    export DATABASE_URL="postgresql://user:pass@host/db"
    python3 contest/tests/test_contest_sheet_logic.py

This does NOT prove the live Google Sheets API calls work (gspread.Client,
auth, actual HTTP) — it proves the column-insertion/row-matching/summary
math is correct, which is the part most likely to have an off-by-one or
duplicate-column bug. Pair this with one real manual test against the
actual sheet before relying on it with real students.

Sheet layout is now TWO header rows (see contest_sheet.py's module
docstring): row 1 = contest dates, row 2 = Reg No / contest codes /
summary columns / _user_id. Data starts at row 3. Cells are blank for
"didn't solve anything" (blank and "0" mean the same thing) instead of
the old "ABS" marker, and a contest cell can be "N" (solved N during the
window) or "N(M)" (solved N in-window, M more after the window, same day).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contest.tests.fake_worksheet import FakeWorksheet
from contest import contest_sheet as cs
from database.db import get_db
from datetime import date


def _seed_students():
    with get_db() as db:
        db.execute("DELETE FROM users WHERE username IN ('akash_t','mohan_t','jeevan_t')")
        db.execute("""
            INSERT INTO users (username,email,password,is_verified,status,enabled_platforms,
                                created_at,full_name,reg_no,roll_no,branch)
            VALUES
            ('akash_t','a@t.com','x',1,'active','[]','2026-01-01','Akash','312001','24AD001','AI'),
            ('mohan_t','m@t.com','x',1,'active','[]','2026-01-01','Mohan','312002','24AD002','AI'),
            ('jeevan_t','j@t.com','x',1,'active','[]','2026-01-01','Jeevan','','24AM003','AIML')
        """)  # jeevan_t deliberately has no reg_no — the not-yet-backfilled case
        db.commit()


def _seed_sheet():
    sheet = FakeWorksheet()
    code_header = cs.BASE_COLUMNS + cs.SUMMARY_COLUMNS + [cs.KEY_COLUMN]
    date_header = [""] * len(code_header)
    sheet.append_row(date_header)   # row 1
    sheet.append_row(code_header)   # row 2
    return sheet


def run():
    _seed_students()
    sheet = _seed_sheet()

    # 1. Import students with zero contest columns yet
    added = cs.import_students(sheet)
    assert added >= 3, f"expected at least 3 students imported, got {added}"
    usernames_in_sheet = {row[2] for row in sheet.rows[cs.FIRST_DATA_ROW - 1:]}  # Name column
    assert {"Akash", "Mohan", "Jeevan"}.issubset(usernames_in_sheet), usernames_in_sheet
    row_count_after_first_import = len(sheet.rows)
    for row in sheet.rows[cs.FIRST_DATA_ROW - 1:]:
        assert row[-1].isdigit(), f"missing _user_id: {row}"
    print(f"[ok] import_students: {added} students added (>=3 expected), no contest columns yet")

    # 2. Re-import should be a no-op (no duplicates)
    added_again = cs.import_students(sheet)
    assert added_again == 0, "re-import must not duplicate existing students"
    assert len(sheet.rows) == row_count_after_first_import
    print("[ok] import_students: re-running is a no-op (no duplicate rows)")

    def find_row(name):
        for row in sheet.rows[cs.FIRST_DATA_ROW - 1:]:
            if row[2] == name:
                return row
        raise AssertionError(f"row for {name} not found")

    # 3. Add first contest column, with a date
    created = cs.add_contest_column(sheet, "LC452", date(2026, 4, 4))
    assert created is True
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    date_header_now = sheet.row_values(cs.DATE_HEADER_ROW)
    assert code_header_now == ["Reg No", "Roll No", "Name", "Branch", "LC452",
                                "Total Solved", "Contests Attended", "Attendance %", "_user_id"], code_header_now
    lc_col = code_header_now.index("LC452")
    assert date_header_now[lc_col] == "4 April 2026", date_header_now
    assert find_row("Akash")[lc_col] == ""
    assert find_row("Mohan")[lc_col] == ""
    assert find_row("Jeevan")[lc_col] == ""
    print("[ok] add_contest_column: LC452 inserted before Total Solved, dated, all students blank")

    # 4. Duplicate contest code must be rejected (no-op)
    created_dup = cs.add_contest_column(sheet, "LC452", date(2026, 4, 4))
    assert created_dup is False
    assert sheet.row_values(cs.CODE_HEADER_ROW).count("LC452") == 1, "must never create a duplicate column"
    print("[ok] add_contest_column: duplicate code is a no-op")

    # 5. Simulate a contest sync writing solved counts (this is what
    #    contest_sync.py, Phase 3, actually does — writing straight into
    #    the LC452 cell for each student's row). Akash solved 2 during the
    #    window and 1 more after it; Mohan attended but solved nothing;
    #    Jeevan never attended.
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    lc_col = code_header_now.index("LC452")
    find_row("Akash")[lc_col] = "2(1)"   # 2 in-window, 1 after-window
    find_row("Mohan")[lc_col] = ""       # attended, solved 0 -> blank (blank == 0)
    # Jeevan stays blank — did not attend

    cs.recalculate_summary(sheet)
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    total_idx = code_header_now.index("Total Solved")
    # Akash: cell "2(1)" -> Total Solved counts only the in-window part (2), attended (non-blank)
    assert find_row("Akash")[total_idx:total_idx + 3] == [2, 1, "100%"], find_row("Akash")
    # Mohan: blank cell -> counts as NOT attended (blank == 0 == no data, same meaning)
    assert find_row("Mohan")[total_idx:total_idx + 3] == [0, 0, "0%"], find_row("Mohan")
    assert find_row("Jeevan")[total_idx:total_idx + 3] == [0, 0, "0%"], find_row("Jeevan")
    print("[ok] recalculate_summary: Total Solved / Attended / Attendance% correct, N(M) parsed correctly")

    # 6. Add a second contest column, confirm existing data isn't clobbered
    cs.add_contest_column(sheet, "CF1093", date(2026, 4, 7))
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    date_header_now = sheet.row_values(cs.DATE_HEADER_ROW)
    assert code_header_now.index("CF1093") == code_header_now.index("Total Solved") - 1
    assert code_header_now.index("LC452") < code_header_now.index("CF1093"), "columns append in creation order"
    assert date_header_now[code_header_now.index("CF1093")] == "7 April 2026"
    lc_col_new = code_header_now.index("LC452")
    assert find_row("Akash")[lc_col_new] == "2(1)", "existing contest data must survive a new column insert"
    cf_col = code_header_now.index("CF1093")
    assert find_row("Akash")[cf_col] == "", "new column initializes existing students to blank"
    print("[ok] add_contest_column: second contest preserves first contest's data, correct ordering")

    # 7. Recalculate again with two contest columns, mixed participation
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    cf_col = code_header_now.index("CF1093")
    find_row("Mohan")[cf_col] = "2"  # Mohan solved 2 in CF1093, all in-window
    cs.recalculate_summary(sheet)
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    total_idx = code_header_now.index("Total Solved")
    # Mohan: LC452=blank (not attended), CF1093=2 (attended) -> total=2, attended=1/2=50%
    assert find_row("Mohan")[total_idx:total_idx + 3] == [2, 1, "50%"], find_row("Mohan")
    # Akash: LC452="2(1)" (attended, 2 counts toward total), CF1093=blank (not attended) -> total=2, attended=1/2=50%
    assert find_row("Akash")[total_idx:total_idx + 3] == [2, 1, "50%"], find_row("Akash")
    print("[ok] recalculate_summary: correct across two contests with mixed attendance and N(M) cells")

    # 8. A brand-new student joining after 2 contests exist should backfill blank for both
    with get_db() as db:
        db.execute("""
            INSERT INTO users (username,email,password,is_verified,status,enabled_platforms,
                                created_at,full_name,reg_no,roll_no,branch)
            VALUES ('newkid_t','n@t.com','x',1,'active','[]','2026-02-01','New Kid','312004','24AD004','AI')
        """)
        db.commit()
    added = cs.import_students(sheet)
    assert added == 1
    new_row = find_row("New Kid")
    code_header_now = sheet.row_values(cs.CODE_HEADER_ROW)
    lc_idx = code_header_now.index("LC452")
    cf_idx = code_header_now.index("CF1093")
    assert new_row[lc_idx] == "" and new_row[cf_idx] == "", \
        f"new student must be backfilled blank for existing contests: {new_row}"
    print("[ok] import_students: new student backfilled blank for both existing contests")

    with get_db() as db:
        db.execute("DELETE FROM users WHERE username IN ('akash_t','mohan_t','jeevan_t','newkid_t')")
        db.commit()

    print("\nALL CONTEST SHEET LOGIC TESTS PASSED")


if __name__ == "__main__":
    run()