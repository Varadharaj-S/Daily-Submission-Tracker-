-- =============================================================================
-- DSA Tracker v4 — Contest Tracker, Migration 0002: contest_events + contest_results
-- =============================================================================
-- Additive and idempotent, same pattern as 0001_phase1_schema_hardening.sql.
-- Phase 1 scope only: tables + indexes + FKs. No Google Sheets, no platform
-- API columns populated yet — contest_results exists now so Phase 3 (platform
-- sync) doesn't need another schema migration later.
--
-- Real DATE/TIME/TIMESTAMPTZ types are used here (unlike submissions.solved_date,
-- which is legacy TEXT) since this is a new feature with no existing data to
-- stay compatible with — see the Phase 1 indexes.sql note on that bug for why
-- this matters.
-- =============================================================================

CREATE TABLE IF NOT EXISTS contest_events (
    id           SERIAL PRIMARY KEY,
    contest_name TEXT        NOT NULL,
    contest_code TEXT        NOT NULL,
    platform     TEXT        NOT NULL,           -- 'Codeforces' | 'LeetCode' | 'AtCoder'
    contest_date DATE        NOT NULL,
    start_time   TIME        NOT NULL,
    end_time     TIME        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'Upcoming',  -- 'Upcoming' | 'Running' | 'Completed'
    sheet_name   TEXT        DEFAULT 'Student_Contest',    -- fixed, single sheet (Phase 2)
    created_by   INTEGER     REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contest_code)
);
CREATE INDEX IF NOT EXISTS idx_contest_events_date   ON contest_events(contest_date DESC);
CREATE INDEX IF NOT EXISTS idx_contest_events_status ON contest_events(status);

CREATE TABLE IF NOT EXISTS contest_results (
    id           SERIAL PRIMARY KEY,
    contest_id   INTEGER     NOT NULL REFERENCES contest_events(id) ON DELETE CASCADE,
    user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username     TEXT        NOT NULL,
    solved       INTEGER     DEFAULT 0,
    attendance   BOOLEAN     DEFAULT false,
    rank         INTEGER,
    score        NUMERIC,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contest_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_contest_results_contest ON contest_results(contest_id);
CREATE INDEX IF NOT EXISTS idx_contest_results_user    ON contest_results(user_id);
