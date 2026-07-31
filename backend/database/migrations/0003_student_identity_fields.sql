-- =============================================================================
-- DSA Tracker v4 — Contest Tracker, Migration 0003: student identity fields
-- =============================================================================
-- Additive, idempotent. Adds the fields the Student_Contest Google Sheet
-- needs (Reg No / Roll No / Name / Branch) that don't exist on `users` yet.
-- All nullable — existing users get backfilled via the new
-- /admin/students page, new signups fill them in going forward.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS reg_no     TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS roll_no    TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS branch     TEXT DEFAULT '';

-- reg_no is the primary display key on the Student_Contest sheet once
-- backfilled, so it should be unique when set — but NULL/'' during the
-- backfill period for existing users, so this is a partial unique index
-- (ignores blanks) rather than a plain UNIQUE constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_reg_no_unique
    ON users(reg_no) WHERE reg_no IS NOT NULL AND reg_no != '';
