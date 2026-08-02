-- =============================================================================
-- DSA Tracker v4 — Contest Phase 3 Schema: contest_problems table +
-- synced/sync tracking columns on contest_events
-- =============================================================================
-- These columns and the contest_problems table are referenced by:
--   contest/contest_service.py  (get_due_contests, get_contest_problems,
--                                add_problem_to_contest, remove_problem_from_contest,
--                                force_resync)
--   contest/contest_sync.py     (grading, sync status updates)
--   routes/contest.py           (contest_sync_now, contest_problems,
--                                contest_add_problem, contest_remove_problem)
--   routes/cron.py              (Vercel Cron daily sync)
-- but were never added to any migration — causing every sync attempt to
-- fail with a DB error and contests to stay stuck on "Pending" forever.
-- =============================================================================

-- 1. Add sync-tracking columns to contest_events (idempotent via IF NOT EXISTS)
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS synced          BOOLEAN     NOT NULL DEFAULT FALSE;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS sync_attempts   INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS last_sync_error TEXT;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS sync_claimed_at TIMESTAMPTZ;
ALTER TABLE contest_events ADD COLUMN IF NOT EXISTS last_synced_at  TIMESTAMPTZ;

-- 2. Index to efficiently find unsynced completed contests
CREATE INDEX IF NOT EXISTS idx_contest_events_synced ON contest_events(synced, sync_attempts);

-- 3. contest_problems — the set of problem IDs a contest is graded against
CREATE TABLE IF NOT EXISTS contest_problems (
    id          SERIAL  PRIMARY KEY,
    contest_id  INTEGER NOT NULL REFERENCES contest_events(id) ON DELETE CASCADE,
    problem_id  TEXT    NOT NULL,
    platform    TEXT    NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contest_problems_unique
    ON contest_problems(contest_id, problem_id);
CREATE INDEX IF NOT EXISTS idx_contest_problems_contest
    ON contest_problems(contest_id);
