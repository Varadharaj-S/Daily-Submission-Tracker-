-- =============================================================================
-- DSA Tracker v4 — Migration 0005: reconcile submitted_at, add after_window
-- =============================================================================
-- Additive, idempotent.
--
-- `submissions.submitted_at` already exists on the live DB — confirmed via
-- SQL Editor on 2026-08-02 — but it was never in database/db.py's tracked
-- CREATE TABLE, so this migration file (and everything that assumed the
-- column didn't exist) was written against a stale picture of the schema.
-- This statement is a no-op against the live DB; it exists so the tracked
-- schema catches up to reality, and so a fresh/dev database ends up with
-- the same column. `ADD COLUMN IF NOT EXISTS` does not touch/retype an
-- already-existing column, so this is safe either way.
--
-- Known issue, not fixed by this migration alone: submitted_at is
-- inconsistently populated — normal_sync.py's incremental path and
-- bot_sheet_sync.py's full-import path didn't both set it (see that
-- conversation's "submitted_at NULL bug" notes). Both are fixed as of
-- this same change (normal_sync.py + bot_sheet_sync.py now capture the
-- platform's epoch timestamp and set submitted_at on every insert), but
-- rows already in the DB from before that fix keep submitted_at = NULL —
-- there's no way to derive the exact time retroactively, since the raw
-- epoch was never stored for them. contest_sync.py treats NULL
-- submitted_at as "date known, exact time unknown" and falls back to
-- day-level matching for those rows (see contest_sync.py's docstring).
-- =============================================================================

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_sub_submitted_at ON submissions(user_id, platform, submitted_at);

-- contest_sync.py now splits solves into "during the contest window" vs
-- "later, same day" (see that file's module docstring) — this column
-- stores the latter so it survives a re-sync / server restart, same as
-- `solved` already did for the former.
ALTER TABLE contest_results ADD COLUMN IF NOT EXISTS after_window INTEGER DEFAULT 0;