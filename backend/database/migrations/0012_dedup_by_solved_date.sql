-- =============================================================================
-- DSA Tracker v4 — Migration 0012: de-duplicate submissions by date, not just
-- (user, platform, problem)
-- =============================================================================
-- Additive/idempotent, safe to re-run. Also self-heals automatically on every
-- app cold start via database/db.py's ensure_submission_dedup_by_date() —
-- this file exists for parity with `python database/migrate.py` and as the
-- historical record of the change.
--
-- WHY: submissions had UNIQUE(user_id, platform, problem_id), and every
-- INSERT's ON CONFLICT (user_id, platform, problem_id) DO NOTHING relied on
-- it. That blocked re-logging the same problem on a genuinely later date —
-- not just an exact same-day re-fetch, which is the only case that should be
-- a no-op. The row never reached the DB, so the sheet never got a new row or
-- a COUNT bump for that date either.
--
-- FIX: swap it for UNIQUE(user_id, platform, problem_id, solved_on). Same
-- problem + same day still collapses to a no-op; same problem + a different
-- day is now allowed through. Depends on solved_on (migration 0011) already
-- being backfilled.
-- =============================================================================

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'submissions'
          AND con.contype = 'u'
          AND (
              SELECT array_agg(a.attname ORDER BY a.attname)
              FROM unnest(con.conkey) AS k(attnum)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = k.attnum
          ) = ARRAY['platform','problem_id','user_id']::name[]
    LOOP
        EXECUTE format('ALTER TABLE submissions DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;

ALTER TABLE submissions
ADD CONSTRAINT submissions_user_platform_problem_solved_on_key
UNIQUE (user_id, platform, problem_id, solved_on);
