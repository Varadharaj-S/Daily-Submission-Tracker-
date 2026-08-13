"""
contest/tests/test_contest_sheet_logic.py — exercises the real
add_contest_column / import_students / recalculate_summary functions from
contest_sheet.py against a FakeWorksheet (see fake_worksheet.py).

PHASE 2 UPDATE:
  * import_students(sheet, year) now requires a `year` argument — tests
    updated accordingly.
  * Year-isolation tests added:
      - 2028 students only appear in the 2028 sheet
      - 2029 students only appear in the 2029 sheet
      - no cross-year bleed
  * year_sheet_service resolver correctness test added.
  * DB backed by in-memory SQLite (fake_db) — no PostgreSQL needed.
  * gspread backed by stub — no Google Sheets connection needed.

Run (from backend/ directory):
    python3 contest/tests/test_contest_sheet_logic.py
"""

import os
import sys

# ── Path setup: project root (backend/) first, then stubs ───────────────────
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUBS   = os.path.join(BACKEND, "stubs")  # ../stubs relative to backend/
sys.path.insert(0, STUBS)
sys.path.insert(1, BACKEND)

# ── Patch imports BEFORE contest_sheet.py is loaded ─────────────────────────
import gspread  # stub version from stubs/
import fake_db

# Monkey-patch database.db.get_db → fake_db.get_db
import database
import database.db as _real_db
_real_db.get_db = fake_db.get_db
database.db.get_db = fake_db.get_db

# Monkey-patch services.year_sheet_service with fake version
import fake_year_sheet_service
import services.year_sheet_service as _yss
_yss.get_sheet_id_for_year = fake_year_sheet_service.get_sheet_id_for_year
_yss.is_year_configured     = fake_year_sheet_service.is_year_configured
_yss.list_configured_years  = fake_year_sheet_service.list_configured_years

from contest.tests.fake_worksheet import FakeWorksheet
from contest import contest_sheet as cs


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_sheet():
    """A new FakeWorksheet with the standard header row already written."""
    s = FakeWorksheet()
    s.append_row(cs.BASE_COLUMNS + cs.SUMMARY_COLUMNS + [cs.KEY_COLUMN])
    return s


def _seed(username, full_name, reg_no, roll_no, branch, year,
          status="active", is_admin=0):
    db = fake_db.get_db()
    db.execute(
        "INSERT OR IGNORE INTO users "
        "(username,email,password,is_verified,status,is_admin,"
        " full_name,reg_no,roll_no,branch,cohort_year,"
        " enabled_platforms,created_at) "
        "VALUES (?,?,?,1,?,?,?,?,?,?,?,'[]','2026-01-01')",
        (username, f"{username}@t.com", "x",
         status, is_admin,
         full_name, reg_no, roll_no, branch, str(year))
    )
    db.commit()


def _cleanup(*usernames):
    db = fake_db.get_db()
    for u in usernames:
        db.execute("DELETE FROM users WHERE username=?", (u,))
    db.commit()


def _find_row(sheet, name):
    for row in sheet.rows[1:]:
        if row[2] == name:
            return row
    raise AssertionError(f"row for {name!r} not found in sheet")


def _names_in_sheet(sheet):
    return {row[2] for row in sheet.rows[1:]}


# ══════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Original core logic tests, updated for year-aware signature
# ══════════════════════════════════════════════════════════════════════════════

def test_import_and_basic_ops():
    fake_db.reset_db()
    _seed("akash_t",  "Akash",  "312001", "24AD001", "AI",   2028)
    _seed("mohan_t",  "Mohan",  "312002", "24AD002", "AI",   2028)
    _seed("jeevan_t", "Jeevan", "",       "24AM003", "AIML", 2028)

    sheet = _fresh_sheet()

    # 1. Import students — must pass year=2028
    added = cs.import_students(sheet, 2028)
    assert added >= 3, f"expected >=3 students, got {added}"
    assert {"Akash", "Mohan", "Jeevan"}.issubset(_names_in_sheet(sheet)), _names_in_sheet(sheet)
    row_count_after_first = len(sheet.rows)
    for row in sheet.rows[1:]:
        assert row[-1].isdigit(), f"missing _user_id: {row}"
    print(f"[ok] import_students(sheet, year): {added} students added (>=3 expected)")

    # 2. Re-import is a no-op
    added_again = cs.import_students(sheet, 2028)
    assert added_again == 0, "re-import must not duplicate"
    assert len(sheet.rows) == row_count_after_first
    print("[ok] import_students re-run is a no-op (no duplicates)")

    # 3. Add first contest column
    created = cs.add_contest_column(sheet, "LC452")
    assert created is True
    header = sheet.row_values(1)
    assert header == ["Reg No", "Roll No", "Name", "Branch", "LC452",
                      "Total Solved", "Contests Attended", "Attendance %", "_user_id"], header
    lc_col = header.index("LC452")
    assert _find_row(sheet, "Akash")[lc_col]  == "ABS"
    assert _find_row(sheet, "Mohan")[lc_col]  == "ABS"
    assert _find_row(sheet, "Jeevan")[lc_col] == "ABS"
    print("[ok] add_contest_column: LC452 inserted before Total Solved, all ABS")

    # 4. Duplicate column is a no-op
    assert cs.add_contest_column(sheet, "LC452") is False
    assert sheet.row_values(1).count("LC452") == 1
    print("[ok] add_contest_column: duplicate is a no-op")

    # 5. Simulate solved counts, recalculate
    header = sheet.row_values(1)
    lc_col = header.index("LC452")
    _find_row(sheet, "Akash")[lc_col] = "3"
    _find_row(sheet, "Mohan")[lc_col] = "0"
    # Jeevan stays ABS

    cs.recalculate_summary(sheet)
    header = sheet.row_values(1)
    ti = header.index("Total Solved")
    assert _find_row(sheet, "Akash")[ti:ti+3]  == [3, 1, "100%"], _find_row(sheet, "Akash")
    assert _find_row(sheet, "Mohan")[ti:ti+3]  == [0, 1, "100%"], _find_row(sheet, "Mohan")
    assert _find_row(sheet, "Jeevan")[ti:ti+3] == [0, 0, "0%"],   _find_row(sheet, "Jeevan")
    print("[ok] recalculate_summary: correct after 1 contest")

    # 6. Second contest column doesn't clobber existing data
    cs.add_contest_column(sheet, "CF1093")
    header = sheet.row_values(1)
    assert header.index("CF1093") == header.index("Total Solved") - 1
    assert header.index("LC452") < header.index("CF1093")
    lc_col_new = header.index("LC452")
    assert _find_row(sheet, "Akash")[lc_col_new] == "3", "LC452 data survived CF1093 insert"
    cf_col = header.index("CF1093")
    assert _find_row(sheet, "Akash")[cf_col] == "ABS", "new column initialises to ABS"
    print("[ok] add_contest_column: second contest preserves first's data")

    # 7. Recalculate with two contests, mixed attendance
    header = sheet.row_values(1)
    cf_col = header.index("CF1093")
    _find_row(sheet, "Mohan")[cf_col] = "2"
    cs.recalculate_summary(sheet)
    header = sheet.row_values(1)
    ti = header.index("Total Solved")
    assert _find_row(sheet, "Mohan")[ti:ti+3]  == [2, 2, "100%"], _find_row(sheet, "Mohan")
    assert _find_row(sheet, "Akash")[ti:ti+3]  == [3, 1, "50%"],  _find_row(sheet, "Akash")
    print("[ok] recalculate_summary: correct across two contests, mixed attendance")

    # 8. New student joining after 2 contests exist gets ABS backfilled
    _seed("newkid_t", "New Kid", "312004", "24AD004", "AI", 2028)
    added = cs.import_students(sheet, 2028)
    assert added == 1
    new_row = _find_row(sheet, "New Kid")
    header = sheet.row_values(1)
    lc_i = header.index("LC452")
    cf_i = header.index("CF1093")
    assert new_row[lc_i] == "ABS" and new_row[cf_i] == "ABS", \
        f"new student must be backfilled ABS: {new_row}"
    print("[ok] import_students: new student backfilled ABS for existing contests")

    _cleanup("akash_t", "mohan_t", "jeevan_t", "newkid_t")
    print("\n[GROUP 1] All core logic tests PASSED\n")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Year-isolation tests
# ══════════════════════════════════════════════════════════════════════════════

def test_year_isolation():
    fake_db.reset_db()

    # Seed 3 students in 2028, 2 in 2029
    _seed("alice_2028", "Alice",   "2028001", "28AD001", "AI",   2028)
    _seed("bob_2028",   "Bob",     "2028002", "28AD002", "AI",   2028)
    _seed("carol_2028", "Carol",   "2028003", "28AD003", "CSE",  2028)
    _seed("dave_2029",  "Dave",    "2029001", "29AD001", "AI",   2029)
    _seed("eve_2029",   "Eve",     "2029002", "29AD002", "AIML", 2029)

    sheet_2028 = _fresh_sheet()
    sheet_2029 = _fresh_sheet()

    # --- import into each year's sheet ---
    added_2028 = cs.import_students(sheet_2028, 2028)
    added_2029 = cs.import_students(sheet_2029, 2029)

    assert added_2028 == 3, f"2028 sheet should have 3 students, got {added_2028}"
    assert added_2029 == 2, f"2029 sheet should have 2 students, got {added_2029}"
    print(f"[ok] year-isolation: 2028 sheet got {added_2028} students, 2029 sheet got {added_2029}")

    # --- 2028 students are only in 2028 sheet ---
    names_2028 = _names_in_sheet(sheet_2028)
    names_2029 = _names_in_sheet(sheet_2029)

    assert {"Alice", "Bob", "Carol"} == names_2028, \
        f"2028 sheet should have exactly Alice/Bob/Carol, got {names_2028}"
    assert {"Dave", "Eve"} == names_2029, \
        f"2029 sheet should have exactly Dave/Eve, got {names_2029}"
    print("[ok] 2028 students are ONLY in 2028 sheet")
    print("[ok] 2029 students are ONLY in 2029 sheet")

    # --- No cross-year bleed: 2029 students must NOT appear in 2028 sheet ---
    cross_2029_in_2028 = {"Dave", "Eve"} & names_2028
    assert not cross_2029_in_2028, \
        f"2029 students leaked into 2028 sheet: {cross_2029_in_2028}"
    cross_2028_in_2029 = {"Alice", "Bob", "Carol"} & names_2029
    assert not cross_2028_in_2029, \
        f"2028 students leaked into 2029 sheet: {cross_2028_in_2029}"
    print("[ok] No cross-year bleed (2028 ↔ 2029)")

    # --- re-running import with the correct year is a no-op (no duplicates) ---
    added_noop = cs.import_students(sheet_2028, 2028)
    assert added_noop == 0, f"re-import of 2028 into 2028 sheet must be 0, got {added_noop}"
    assert _names_in_sheet(sheet_2028) == {"Alice", "Bob", "Carol"},         f"2028 sheet must still be only Alice/Bob/Carol after no-op re-import"
    added_noop_29 = cs.import_students(sheet_2029, 2029)
    assert added_noop_29 == 0, f"re-import of 2029 into 2029 sheet must be 0, got {added_noop_29}"
    print("[ok] re-import with correct year is a no-op (no duplicates)")

    # --- isolation via DB year filter: a year=2029 import into a fresh sheet
    #     only pulls 2029 students — Alice (2028) cannot appear ---
    sheet_fresh_2029 = _fresh_sheet()
    added_2029_fresh = cs.import_students(sheet_fresh_2029, 2029)
    assert added_2029_fresh == 2, f"fresh year=2029 import should add 2, got {added_2029_fresh}"
    assert "Alice" not in _names_in_sheet(sheet_fresh_2029),         "Alice (2028 student) must not appear in a year=2029 import"
    assert {"Dave", "Eve"} == _names_in_sheet(sheet_fresh_2029),         f"year=2029 import must contain only Dave+Eve: {_names_in_sheet(sheet_fresh_2029)}"
    print("[ok] Student cannot access another year's sheet (year=2029 DB filter excludes 2028 students)")

    # --- 2028 contest → 2028 sheet only ---
    cs.add_contest_column(sheet_2028, "LC500")
    header = sheet_2028.row_values(1)
    assert "LC500" in header, "LC500 must appear in 2028 sheet"
    assert "LC500" not in sheet_2029.row_values(1), "LC500 must NOT appear in 2029 sheet"
    print("[ok] 2028 contest column (LC500) only added to 2028 sheet")

    # --- 2029 contest → 2029 sheet only ---
    cs.add_contest_column(sheet_2029, "CF2000")
    header = sheet_2029.row_values(1)
    assert "CF2000" in header, "CF2000 must appear in 2029 sheet"
    assert "CF2000" not in sheet_2028.row_values(1), "CF2000 must NOT appear in 2028 sheet"
    print("[ok] 2029 contest column (CF2000) only added to 2029 sheet")

    _cleanup("alice_2028", "bob_2028", "carol_2028", "dave_2029", "eve_2029")
    print("\n[GROUP 2] All year-isolation tests PASSED\n")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP 3 — year_sheet_service resolver tests (offline via fake)
# ══════════════════════════════════════════════════════════════════════════════

def test_year_resolver():
    fake_year_sheet_service.clear()

    # Nothing configured yet
    assert fake_year_sheet_service.get_sheet_id_for_year("2028") is None
    assert not fake_year_sheet_service.is_year_configured("2028")
    print("[ok] resolver: unconfigured year returns None / is_configured=False")

    # Configure 2028
    fake_year_sheet_service.configure("2028", "SHEET_ID_2028")
    assert fake_year_sheet_service.get_sheet_id_for_year("2028") == "SHEET_ID_2028"
    assert fake_year_sheet_service.is_year_configured("2028")
    print("[ok] resolver: configured 2028 → SHEET_ID_2028")

    # 2029 is still unconfigured
    assert fake_year_sheet_service.get_sheet_id_for_year("2029") is None
    assert not fake_year_sheet_service.is_year_configured("2029")
    print("[ok] resolver: 2029 still unconfigured (independent of 2028)")

    # Configure 2029
    fake_year_sheet_service.configure("2029", "SHEET_ID_2029")
    assert fake_year_sheet_service.get_sheet_id_for_year("2029") == "SHEET_ID_2029"
    assert fake_year_sheet_service.get_sheet_id_for_year("2028") == "SHEET_ID_2028"
    print("[ok] resolver: 2029 → SHEET_ID_2029, 2028 → SHEET_ID_2028 (independent)")

    # list_configured_years returns both
    years = set(fake_year_sheet_service.list_configured_years())
    assert {"2028", "2029"} == years, f"expected {{2028, 2029}}, got {years}"
    print("[ok] resolver: list_configured_years returns both configured years")

    # 2028 ID ≠ 2029 ID — no mixing
    id_2028 = fake_year_sheet_service.get_sheet_id_for_year("2028")
    id_2029 = fake_year_sheet_service.get_sheet_id_for_year("2029")
    assert id_2028 != id_2029, "2028 and 2029 must resolve to different spreadsheet IDs"
    print("[ok] resolver: 2028 and 2029 resolve to DIFFERENT spreadsheet IDs")

    fake_year_sheet_service.clear()
    print("\n[GROUP 3] All year_sheet_service resolver tests PASSED\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_year_resolver()
    test_import_and_basic_ops()
    test_year_isolation()
    print("=" * 60)
    print("ALL CONTEST SHEET LOGIC TESTS PASSED")
    print("=" * 60)
