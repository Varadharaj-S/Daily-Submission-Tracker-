-- =============================================================================
-- DSA Tracker v4 — Migration 0009: chunked LeetCode import continuation state
-- =============================================================================
-- Additive, idempotent, safe to re-run.
--
-- Fixes the combined-import 504 (Vercel function execution limit) by
-- splitting /import_lc into three independent single-request actions
-- (/import_codeforces, /import_atcoder, /import_leetcode — see
-- routes/sync.py + sync/chunked_import.py). Codeforces and AtCoder each
-- complete in one request already; LeetCode is the one platform whose full
-- history can't safely fit in one request, so it now walks its submission
-- pages in bounded chunks, one user button-press per chunk.
--
-- These two columns are the ONLY new state needed to make that resumable:
--   lc_import_offset    — the LeetCode submissions-API `offset` to resume
--                          from on the next /import_leetcode call. Saved
--                          only after a chunk's DB+Sheet writes succeed, so
--                          a failed request never advances the cursor and
--                          the next press safely retries the same page.
--   lc_import_has_more   — whether there is more LeetCode history left to
--                          walk (1) or the first full import has reached
--                          the end of the user's submission list (0).
--
-- lc_imported (existing column) is set to 1 only once lc_import_has_more
-- reaches 0, matching the old combined-import's "first full import done"
-- semantics exactly — /sync (incremental) still gates on lc_imported.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_import_offset INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS lc_import_has_more INTEGER DEFAULT 1;
