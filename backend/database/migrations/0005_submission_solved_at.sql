-- =============================================================================
-- DSA Tracker v4 — Migration 0005: exact submission timestamp
-- =============================================================================
-- Additive, idempotent. Needed for contest grading with a real start/end
-- time window (contest/contest_sync.py): `submissions.solved_date` is a
-- text DD-MM-YYYY/ISO date with no time-of-day, so there's no way to tell
-- whether a problem was solved during the contest window or later the
-- same day. This adds a real timestamp column going forward.
--
-- Existing rows are NOT backfilled — the platforms' epoch timestamps
-- were never stored, so there's nothing to derive an exact time from for
-- old submissions. Those rows keep solved_at = NULL; contest_sync.py
-- treats NULL solved_at as "date known, exact time unknown" and falls
-- back to day-level matching for them (see contest_sync.py comments).
-- =============================================================================

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS solved_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_sub_solved_at ON submissions(user_id, platform, solved_at);

-- contest_sync.py now splits solves into "during the contest window" vs
-- "later, same day" (see that file's module docstring) — this column
-- stores the latter so it survives a re-sync / server restart, same as
-- `solved` already did for the former.
ALTER TABLE contest_results ADD COLUMN IF NOT EXISTS after_window INTEGER DEFAULT 0;
