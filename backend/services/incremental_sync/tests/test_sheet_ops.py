"""
services/incremental_sync/tests/test_sheet_ops.py — exercises the real
append_merged() against FakeWorksheet. Run:

    python3 services/incremental_sync/tests/test_sheet_ops.py

No DATABASE_URL needed — sheet_ops.py's logic doesn't touch Postgres.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.incremental_sync.tests.fake_worksheet import FakeWorksheet
from services.incremental_sync import sheet_ops as so


def _item(platform, pid, name, date_str, difficulty="Easy", tags="General"):
    epoch = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
    return {
        "platform": platform, "problem_id": pid, "problem_name": name,
        "problem_url": f"https://x/{pid}", "submission_url": f"https://x/{pid}/sub",
        "difficulty": difficulty, "tags": tags,
        "solved_date": datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y"),
        "solved_epoch": epoch,
    }


def run():
    # 1. First-ever sync: empty sheet, one problem on one date -> new block, count=1, NOT merged
    sheet = FakeWorksheet()
    stats = so.append_merged(sheet, [_item("Codeforces", "1-A", "Problem A", "2026-07-10")])
    assert stats == {"appended": 1, "blocks_extended": 0, "blocks_created": 1}, stats
    assert len(sheet.rows) == 2  # header + 1
    assert sheet.rows[1][so.COUNT_COL - 1] == 1
    assert not sheet.merges, "single-row block should not be merged"
    print("[ok] first sync, single problem: new block, count=1, no merge needed")

    # 2. Same-day sync later: 2 more problems on 2026-07-10 -> extends the bottom block
    stats = so.append_merged(sheet, [
        _item("LeetCode", "two-sum", "Two Sum", "2026-07-10"),
        _item("Codeforces", "1-B", "Problem B", "2026-07-10"),
    ])
    assert stats == {"appended": 2, "blocks_extended": 1, "blocks_created": 0}, stats
    assert len(sheet.rows) == 4  # header + 3 total for that date
    assert sheet.rows[1][so.COUNT_COL - 1] == 3, "count must reflect the FULL block total, not just new rows"
    assert ("G", 2, 4) in sheet.merges, sheet.merges
    print("[ok] same-day extension: block grows to 3 rows, count correctly updated to 3, merge range correct")

    # 3. New day: 2026-07-11, 1 problem -> new block at bottom, old block untouched
    stats = so.append_merged(sheet, [_item("AtCoder", "abc1_a", "Problem C", "2026-07-11")])
    assert stats == {"appended": 1, "blocks_extended": 0, "blocks_created": 1}, stats
    assert len(sheet.rows) == 5
    # old block's merge must still be exactly rows 2-4, untouched
    assert ("G", 2, 4) in sheet.merges, "old block's merge must survive a new block being added"
    assert sheet.rows[1][so.COUNT_COL - 1] == 3, "old block's count must not have changed"
    assert sheet.rows[4][so.COUNT_COL - 1] == 1
    print("[ok] new date after an existing block: new 1-row block created, old block's merge/count untouched")

    # 4. Extend the 07-11 block further -> its own merge, independent of 07-10's merge
    stats = so.append_merged(sheet, [_item("Codeforces", "2-A", "Problem D", "2026-07-11")])
    assert stats == {"appended": 1, "blocks_extended": 1, "blocks_created": 0}, stats
    assert sheet.rows[4][so.COUNT_COL - 1] == 2
    assert ("G", 5, 6) in sheet.merges
    assert ("G", 2, 4) in sheet.merges, "extending the newer block must not disturb the older block's merge"
    print("[ok] extending a later block leaves an earlier block's merge alone")

    # 5. Multi-day catch-up in one call (user didn't sync for 2 days): processes oldest-to-newest
    sheet2 = FakeWorksheet()
    items = [
        _item("Codeforces", "3-A", "E", "2026-07-13"),
        _item("LeetCode", "three-sum", "F", "2026-07-12"),  # out of order on purpose
        _item("Codeforces", "3-B", "G", "2026-07-13"),
    ]
    stats = so.append_merged(sheet2, items)
    assert stats["blocks_created"] == 2, stats
    dates_in_order = [r[0] for r in sheet2.rows[1:]]
    assert dates_in_order == ["2026-07-12", "2026-07-13", "2026-07-13"], \
        f"must process oldest date first regardless of input order: {dates_in_order}"
    print("[ok] multi-day catch-up: processed oldest-to-newest, correct grouping despite unsorted input")

    # 6. Duplicate contiguous-date guard: a date appearing earlier non-contiguously
    #    (shouldn't happen in practice, but the block-finder must only look at the
    #    trailing contiguous run, not scan the whole sheet)
    sheet3 = FakeWorksheet()
    sheet3.rows.append(["2026-01-01", "Old", "u", "Easy", "CF", "t", 1])       # historical, unrelated
    sheet3.rows.append(["2026-07-10", "X", "u", "Easy", "CF", "t", 1])        # current bottom block start
    stats = so.append_merged(sheet3, [_item("Codeforces", "9-A", "Y", "2026-07-10")])
    assert stats["blocks_extended"] == 1
    # block start must be row 3 (the contiguous 07-10 run), not row 2 (unrelated old date)
    assert ("G", 3, 4) in sheet3.merges, sheet3.merges
    assert sheet3.rows[0][0] == "DATE" and sheet3.rows[1][0] == "2026-01-01", \
        "unrelated historical row must be completely untouched"
    print("[ok] block-finder only extends the trailing contiguous block, ignores unrelated historical rows")

    print("\nALL SHEET_OPS TESTS PASSED")


if __name__ == "__main__":
    run()
