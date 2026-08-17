-- =============================================================================
-- DSA Tracker v4 — Migration 0011: solved_on (real DATE) + journey_start_date
-- =============================================================================
-- Additive, idempotent, safe to re-run. Also self-heals automatically on every
-- app cold start via database/db.py's ensure_db_columns() + 
-- ensure_solved_on_backfilled() — this file exists for parity with
-- `python database/migrate.py` and as the historical record of the change.
--
-- WHY: submissions.solved_date is free-form TEXT, written as 'DD-MM-YYYY' by
-- normal_sync.py (the production sync path) but as 'YYYY-MM-DD' by
-- bot_sheet_sync.py / sync/*.py / services/incremental_sync/ (see the
-- long-standing note in database/indexes.sql). Every report query that did
-- `WHERE solved_date BETWEEN ? AND ?` was comparing two different string
-- formats lexicographically — silently wrong for any date range that spans a
-- month/year boundary, and the weekly/journey progress report needs correct
-- per-day and per-week boundaries to work at all.
--
-- FIX: a real `solved_on DATE` column, backfilled here by parsing whichever
-- of the two formats each row actually used, plus an index so date-range
-- report queries stay fast. Every INSERT path going forward (normal_sync.py,
-- bot_sheet_sync.py, sync/chunked_import.py) now writes solved_on alongside
-- the legacy solved_date, so no further backfill should ever be needed for
-- new rows — but the backfill UPDATE is safe to re-run (WHERE solved_on IS
-- NULL) in case an older sync path is ever re-enabled.
--
-- journey_start_date: the date a student's DSA journey/progress report
-- should start counting weeks from. Student-settable (see /api/journey_start
-- in routes/reports.py); defaults to their signup date (created_at) here and
-- in the self-healing version so every existing user gets a sane report
-- immediately, with zero required action.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS journey_start_date DATE;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS solved_on DATE;

UPDATE submissions
SET solved_on = CASE
    WHEN solved_date ~ '^\d{4}-\d{2}-\d{2}$' THEN solved_date::date
    WHEN solved_date ~ '^\d{2}-\d{2}-\d{4}$' THEN to_date(solved_date, 'DD-MM-YYYY')
    WHEN solved_date ~ '^\d{2}/\d{2}/\d{4}$' THEN to_date(solved_date, 'DD/MM/YYYY')
    ELSE NULL
END
WHERE solved_on IS NULL AND COALESCE(solved_date, '') != '';

CREATE INDEX IF NOT EXISTS idx_sub_user_solved_on ON submissions(user_id, solved_on);

UPDATE users
SET journey_start_date = substring(created_at from 1 for 10)::date
WHERE journey_start_date IS NULL
  AND created_at ~ '^\d{4}-\d{2}-\d{2}';
