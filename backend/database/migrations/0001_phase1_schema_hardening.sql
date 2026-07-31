-- =============================================================================
-- DSA Tracker v4 — Phase 1, Migration 0001: Foreign keys + new cache/log tables
-- =============================================================================
-- Additive and idempotent. Run via database/migrate.py, which does a
-- pre-flight orphan-row check before this file ever touches the DB — if any
-- existing rows point at a user_id/admin_id that no longer exists, the
-- runner aborts and prints them instead of letting an ALTER TABLE fail
-- halfway through.
--
-- Two existing columns use `0` as a "no user" sentinel instead of NULL
-- (daily_challenges.assigned_by, daily_tracker_sheets.created_by). Postgres
-- SERIAL ids start at 1, so 0 never refers to a real user — these are
-- normalized to NULL below before the constraint is added.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Normalize sentinel 0-values to NULL so FKs can be added safely
-- ---------------------------------------------------------------------------
UPDATE daily_challenges     SET assigned_by = NULL WHERE assigned_by = 0;
UPDATE daily_tracker_sheets SET created_by  = NULL WHERE created_by  = 0;

ALTER TABLE daily_challenges     ALTER COLUMN assigned_by SET DEFAULT NULL;
ALTER TABLE daily_tracker_sheets ALTER COLUMN created_by  SET DEFAULT NULL;


-- ---------------------------------------------------------------------------
-- 2. Foreign keys on existing tables
-- ---------------------------------------------------------------------------
-- Wrapped in DO blocks with a duplicate_object guard so this file can be run
-- repeatedly without erroring on constraints that already exist.

DO $$ BEGIN
    ALTER TABLE submissions
        ADD CONSTRAINT fk_submissions_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE follows
        ADD CONSTRAINT fk_follows_follower
        FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE follows
        ADD CONSTRAINT fk_follows_following
        FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Audit trails use SET NULL rather than CASCADE: if an admin account is later
-- deleted, the record that an action happened should survive, just no
-- longer attributed to a live user row.
DO $$ BEGIN
    ALTER TABLE admin_logs
        ADD CONSTRAINT fk_admin_logs_admin
        FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE login_history
        ADD CONSTRAINT fk_login_history_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE sync_logs
        ADD CONSTRAINT fk_sync_logs_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE daily_challenges
        ADD CONSTRAINT fk_daily_challenges_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE daily_challenges
        ADD CONSTRAINT fk_daily_challenges_assigned_by
        FOREIGN KEY (assigned_by) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE mentor_assignments
        ADD CONSTRAINT fk_mentor_assignments_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE mentor_assignments
        ADD CONSTRAINT fk_mentor_assignments_admin
        FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE custom_problems
        ADD CONSTRAINT fk_custom_problems_created_by
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE email_tokens
        ADD CONSTRAINT fk_email_tokens_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE daily_tracker_sheets
        ADD CONSTRAINT fk_tracker_sheets_created_by
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE daily_tracker_sheet_results
        ADD CONSTRAINT fk_tracker_results_sheet
        FOREIGN KEY (sheet_id) REFERENCES daily_tracker_sheets(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE daily_tracker_sheet_results
        ADD CONSTRAINT fk_tracker_results_student
        FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ---------------------------------------------------------------------------
-- 3. New tables (dashboard/leaderboard cache, notifications, weekly reports,
--    sheet backups, activity log, sessions)
-- ---------------------------------------------------------------------------
-- These are inert until Phase 3 wires up the services that read/write them
-- (dashboard cache invalidation on sync, notification creation, etc). Adding
-- them now means Phase 3 is pure application code with no further schema
-- changes needed.

-- One cached, pre-computed dashboard payload per user. Recomputed by the
-- sync worker after each sync; read directly by the dashboard route.
CREATE TABLE IF NOT EXISTS dashboard_cache (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One cached payload per leaderboard "scope" (e.g. 'global', 'weekly_2026_W28')
-- rather than per user, since a leaderboard is inherently a cross-user view.
CREATE TABLE IF NOT EXISTS leaderboard_cache (
    scope        TEXT        PRIMARY KEY,
    payload      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weekly_reports (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week_start   DATE        NOT NULL,
    week_end     DATE        NOT NULL,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, week_start)
);
CREATE INDEX IF NOT EXISTS idx_weekly_reports_user ON weekly_reports(user_id, week_start DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type         TEXT        NOT NULL,
    title        TEXT        NOT NULL,
    body         TEXT        DEFAULT '',
    link         TEXT        DEFAULT '',
    is_read      BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS sheet_backups (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sheet_id     TEXT        DEFAULT '',
    backup_path  TEXT        DEFAULT '',
    row_count    INTEGER     DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sheet_backups_user ON sheet_backups(user_id, created_at DESC);

-- General user activity trail (logins, syncs, profile edits, etc) — distinct
-- from admin_logs, which is specifically admin actions. user_id uses
-- ON DELETE SET NULL rather than CASCADE so the activity trail survives
-- account deletion for audit purposes.
CREATE TABLE IF NOT EXISTS activity_logs (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INTEGER     REFERENCES users(id) ON DELETE SET NULL,
    action       TEXT        NOT NULL,
    metadata     JSONB       DEFAULT '{}'::jsonb,
    ip           TEXT        DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_logs_user    ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_logs_created ON activity_logs(created_at);

-- Server-side session store, for when auth moves off client-side cookies
-- only (refresh tokens / "log out all devices" support).
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT        PRIMARY KEY,
    user_id      INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_agent   TEXT        DEFAULT '',
    ip           TEXT        DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked      BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
