-- =============================================================================
-- DSA Tracker v4 — Migration 0004: per-platform last-sync timestamps
-- =============================================================================
-- Additive, idempotent. Needed for incremental sync (services/incremental_sync.py):
-- without a per-platform "since" timestamp, every sync has to re-download a
-- user's entire submission history to find what's new.
--
-- This does NOT touch the existing `last_sync` column (used by the current
-- production sync path in normal_sync.py) — that stays exactly as-is. These
-- are new, separate columns only the new incremental module reads/writes.
-- =============================================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_cf_sync TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_lc_sync TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ac_sync TIMESTAMPTZ;
