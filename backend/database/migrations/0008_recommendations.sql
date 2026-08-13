-- =============================================================================
-- DSA Tracker v4 — Phase 3 Migration 0008: recommendations table
-- =============================================================================
-- Additive and idempotent, same pattern as 0002_contest_tracker.sql /
-- 0006_contest_phase3_schema.sql: this is the formal migration counterpart
-- of database/db.py's ensure_recommendation_schema(), which already creates
-- this table unconditionally on cold start (Vercel never runs this file
-- automatically — see database/migrate.py's module docstring). Both are
-- kept, same as ensure_contest_schema() + this migration file's contest_*
-- counterparts: ensure_recommendation_schema() guarantees prod never 500s
-- on a fresh cold start, this file is the reviewable, ordered schema record
-- for anyone running `python database/migrate.py` by hand.
--
-- ONE reusable table for every cohort year — year isolation is a
-- cohort_year WHERE-clause concern (see routes/recommendations.py),
-- never a per-year table (no recommendations_2028, recommendations_2029, ...).
-- =============================================================================

CREATE TABLE IF NOT EXISTS recommendations (
    id           SERIAL PRIMARY KEY,
    cohort_year  TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    category     TEXT DEFAULT 'Announcement',
    external_url TEXT DEFAULT '',
    image_url    TEXT DEFAULT '',
    created_by   INTEGER,
    created_at   TEXT DEFAULT '',
    updated_at   TEXT DEFAULT '',
    published    INTEGER DEFAULT 1,
    pinned       INTEGER DEFAULT 0
);

-- Student feed lookup: WHERE cohort_year = <current_user.cohort_year>
CREATE INDEX IF NOT EXISTS idx_reco_cohort_year ON recommendations(cohort_year);

-- Student feed lookup with the published filter applied (the actual
-- query shape used by routes/recommendations.py's student_recommendations()).
CREATE INDEX IF NOT EXISTS idx_reco_cohort_published ON recommendations(cohort_year, published);

-- Category filter tabs/dropdown (mentor + student).
CREATE INDEX IF NOT EXISTS idx_reco_category ON recommendations(category);
