# Incremental Sync — status and swap-over plan

## Status: built and tested standalone. NOT wired into any route yet.

`normal_sync.py` is still the live production sync path (`routes/sync.py`,
`routes/admin.py`, `services/scheduler_service.py` all call it via
`services/sync_engine.py`). This module was built alongside it, untouched,
per the plan: get it fully correct and tested first, then do a single,
reversible swap.

## What's been verified vs. what hasn't

**Verified for real, in this repo's test environment:**
- `sheet_ops.py`'s date-block merge/count logic — 6 scenario tests against
  an in-memory fake worksheet with real grid + merge-range semantics
  (`tests/test_sheet_ops.py`). Caught and fixed one real off-by-one bug
  in block-boundary detection before it could reach a live sheet.
- `db_ops.py`'s dedup/insert and last-sync timestamp tracking — 5 tests
  against a real Postgres instance (`tests/test_db_ops.py`), including the
  case that matters most: re-fetching an overlapping window inserts
  nothing and reports zero new items.
- The whole `services/incremental_sync` package imports cleanly and
  doesn't affect `app.py` boot or route count — confirmed nothing here
  touches the live app by accident.

**NOT verified — needs one real test run before swap-over:**
- `fetchers.py`'s actual HTTP calls to Codeforces, the LeetCode mirror,
  and kenkoooo (AtCoder). The parameters (`count=`, `from_second=`) are
  copied from documented, already-in-production endpoints, but this
  sandbox's network access doesn't reach any of them, so the *values*
  that come back have never been inspected here.
- Live Google Sheets writes — same limitation as the Contest Tracker work.
  `get_or_create_user_sheet()` reuses `normal_sync.get_sheet()` exactly,
  which is real production code, so the auth/open path is trusted; the
  new `update_cell`/`merge_cells`/`unmerge_cells` calls in `sheet_ops.py`
  are not yet confirmed against a real sheet.

## Before swapping in: a real test run

1. Pick one test account with a decent CF/LC/AC history.
2. In a shell with `DATABASE_URL` and the Google credentials available:
   ```python
   from services.incremental_sync import sync_user_incremental
   from database.db import get_db
   with get_db() as db:
       user = dict(db.execute("SELECT * FROM users WHERE username=?", ("your_test_user",)).fetchone())
   result = sync_user_incremental(user)
   print(result)
   ```
3. Run it twice in a row. The second run's `new_count` should be 0 — that's
   the whole point of `last_cf_sync`/`last_lc_sync`/`last_ac_sync`.
4. Open the actual sheet tab and check: correct dates, correct merged
   COUNT per date-block (compare against how many problems were actually
   solved that day — this is the bug being fixed vs. current production,
   where COUNT is always 1).
5. Solve one more problem on Codeforces, run it a third time, confirm
   exactly one new row appears and the day's block+count updates instead
   of creating a duplicate block.

## The swap itself, once step 3 above is clean

One line, in `services/sync_engine.py`:

```python
# before
from normal_sync import sync_user_data

# after
from services.incremental_sync import sync_user_incremental as sync_user_data
```

Every caller (`routes/sync.py`, `routes/admin.py`,
`services/scheduler_service.py`) imports `sync_user_data` from
`services.sync_engine`, not from `normal_sync` directly — so this one
line is the entire swap. `normal_sync.py` itself stays in the repo
untouched and can be the rollback path (revert the one line) if anything
looks wrong after the switch.

## Known limitations carried over intentionally

- **LeetCode has no real incremental fetch.** The third-party mirror this
  app already depends on only returns the most recent ~20 accepted
  submissions, no `since` parameter exists. This isn't a regression from
  `normal_sync.py` — it's the same ceiling that code has too. Dedup at
  insert time is what actually prevents reprocessing, same as today.
- **Codeforces incremental fetch has a ceiling** (`max_count=500` per
  sync by default). A user who solves 500+ problems on Codeforces between
  two syncs would have some missed — logged as a warning, not silent.
  Raise `max_count` if a use case actually needs it; 500 between syncs is
  an extreme edge case for a student tracker.
