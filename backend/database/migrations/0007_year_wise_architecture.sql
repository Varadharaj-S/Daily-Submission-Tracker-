-- =============================================================================
-- DSA Tracker v4 — Migration 0007: year-wise architecture (Phase 2)
-- =============================================================================
-- Additive, idempotent, safe to re-run. This is the SQL-file counterpart of
-- database/db.py's ensure_year_schema() (which already runs unconditionally
-- on every app.py cold start, including Vercel) — that function was NOT
-- previously reachable through database/migrate.py, so anyone provisioning
-- a database by running the migration script directly (rather than just
-- starting the app once) would end up missing this schema. Both paths now
-- create the exact same thing.
--
-- Required schema:
--   * users.cohort_year — which year/cohort a student belongs to. Nullable:
--     existing students created before this migration stay NULL/unassigned
--     rather than being guessed into a year. Nothing in this migration
--     ever writes a value into an existing row.
--   * year_sheets — the single source of truth mapping a year/cohort to
--     the Google Spreadsheet that year's students/contests live in
--     (see services/year_sheet_service.py — the ONLY module that should
--     ever read/write this table).
--   * an index on users.cohort_year, since every year-scoped query in the
--     app (routes/admin.py mentor endpoints, contest_sheet.py's
--     import_students, etc.) filters WHERE cohort_year = ?.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS cohort_year TEXT;

CREATE TABLE IF NOT EXISTS year_sheets (
    year           TEXT PRIMARY KEY,
    spreadsheet_id TEXT NOT NULL,
    updated_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_cohort_year ON users(cohort_year);
