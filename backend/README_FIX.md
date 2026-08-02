# Contest Sync Fix — what was broken, what changed

## Root causes found (your live repo, the flat/DSA_TRACKER.zip one)

1. **`contest_service.py` was missing 5 functions** that `contest/contest_sync.py`
   already imports and calls: `get_due_contests()`, `get_contest_problems()`,
   `add_problem_to_contest()`, `remove_problem_from_contest()`, `force_resync()`.
   None of them existed — so `sync_one_contest()` crashed with an
   `AttributeError` the instant it ran, before it ever got to grading anything.

2. **`contest_utils.py` was missing `get_contest_window()`**, which
   `contest_sync.py` also imports. Same story — dead import, instant crash.

3. **`contest_sheet.py` was missing `write_contest_results()`** — the function
   that actually pushes graded numbers into the Student_Contest sheet.
   `contest_sync.py` calls it but it was never written. I added it and
   smoke-tested it offline against the repo's own `FakeWorksheet` test double
   (no live Sheets connection needed for that) — confirmed a participant's
   solved count gets written and `Total Solved / Contests Attended /
   Attendance %` recalculate correctly, while non-participants stay `ABS`.

4. **`routes/contest.py` never exposed any of Phase 3** — no `Sync Now`, no
   `Problems` add/remove routes. The dashboard only ever showed Delete. All
   four routes are added now: `/contest/sync_now/<cid>`,
   `/contest/problems/<cid>` (view), `/contest/problems/<cid>/add`,
   `/contest/problems/<cid>/remove`.

5. **`templates/contest_dashboard.html` never rendered any of it** — added
   Problems count + Sync status columns, and Sync Now / Problems buttons per
   row. New `templates/contest_problems.html` page for adding/removing a
   contest's problem list.

6. **The 5-minute contest-sync cron job was never actually in `render.yaml`**
   — `contest_scheduler.py`'s own docstring assumes a Render Cron job called
   `dsa-tracker-contest-sync` exists; it didn't. Added it (`*/5 * * * *`,
   `python contest_scheduler.py`). Without this, sync only ever ran when an
   admin clicked "Sync Now" by hand — never automatically.

## Files in this folder (drop these into your repo, same paths)

```
contest/contest_utils.py       — added get_contest_window(), normalize_problem_code()
contest/contest_service.py     — added the 5 missing Phase 3 functions
contest/contest_sheet.py       — added write_contest_results(); hardened recalculate_summary()
routes/contest.py              — added Sync Now + Problems routes, problem_count on dashboard
templates/contest_dashboard.html — Problems/Sync columns + Sync Now/Problems buttons
templates/contest_problems.html  — new page: add/remove a contest's problem list
render.yaml                    — added the missing contest-sync cron job
```

## Before you deploy

- These files were generated from a copy with `.env` and the Google
  service-account JSON **removed** — put your real ones back locally, don't
  commit them.
- **Rotate the service-account key and your DB password** — both were shared
  in this chat as real files, treat them as compromised.
- I couldn't test against your live Postgres or Google Sheets (no network
  access to either from where I'm running) — the Python logic is verified to
  compile and the sheet-writing logic is verified against the repo's own
  offline test double, but do one real "Sync Now" click after deploying and
  check the actual sheet, same as the original file's own docstring already
  recommended.
- `contest_problems` table: the live schema you showed me uses columns
  `(contest_id, problem_id, platform)` — that's what all this code assumes.
  `database/migrations/0007_contest_problems_db_sync.sql` in your repo
  describes different column names (`problem_code`, `problem_name`,
  `problem_url`) — that migration doesn't match what's actually live. Worth
  reconciling those so a future fresh deploy doesn't create the wrong schema.
