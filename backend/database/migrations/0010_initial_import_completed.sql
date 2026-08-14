-- =============================================================================
-- DSA Tracker v4 — Migration 0010: persisted "Import LC" completion state
-- =============================================================================
-- Additive, idempotent, safe to re-run.
--
-- The one-click "Import LC" button (frontend orchestrator in main.js) drives
-- THREE independent single-request endpoints in sequence:
--   /import_codeforces -> /import_atcoder -> /import_leetcode (chunked, one
--   or more requests until the full history is walked).
--
-- Each of those endpoints already persists its own platform-level result
-- (lc_imported existed already; this migration adds the same thing for the
-- other two platforms):
--   cf_imported — 1 once /import_codeforces has actually succeeded for this
--                 user (at least once; a later /import_codeforces call keeps
--                 it at 1 even if that particular call fails, since a first
--                 successful import already happened).
--   ac_imported — same, for /import_atcoder.
--   lc_imported — existing column; 1 once lc_import_has_more reaches 0.
--
-- initial_import_completed is the single persisted source of truth the
-- frontend uses to decide whether to show the "Import LC" button at all
-- (GET /dashboard already returns "SELECT * FROM users", so this column is
-- exposed with no route change needed). It is set to 1 ONLY when
-- cf_imported AND ac_imported AND lc_imported are ALL 1 — never on a
-- partial run, and it is computed/persisted server-side after every
-- successful platform call, so a page refresh, logout/login, or a second
-- device always reflects the user's real state instead of trusting the
-- frontend's in-memory sequencing.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS cf_imported INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ac_imported INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS initial_import_completed INTEGER DEFAULT 0;
