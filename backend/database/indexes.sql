-- =============================================================================
-- DSA Tracker v4 — Phase 1: Indexes
-- =============================================================================
-- Safe to run any number of times against the existing production database.
-- Every statement is IF NOT EXISTS, so nothing here is destructive and nothing
-- here changes application behavior — it only makes existing queries faster.
--
-- Apply with: python database/migrate.py
-- =============================================================================

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
-- Looked up on every login (username), password-reset / signup flow (email),
-- admin filtering (status), and the "last active" views (last_login).
CREATE INDEX IF NOT EXISTS idx_users_email       ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_status       ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_last_login   ON users(last_login);

-- ---------------------------------------------------------------------------
-- submissions
-- ---------------------------------------------------------------------------
-- idx_sub_user and idx_sub_platform already exist (created in db.py init_db).
-- These fill the remaining single-column and composite lookups used by the
-- dashboard, profile page, and CSV/sheet export queries.
CREATE INDEX IF NOT EXISTS idx_sub_problem_id      ON submissions(problem_id);
CREATE INDEX IF NOT EXISTS idx_sub_solved_date      ON submissions(solved_date);
CREATE INDEX IF NOT EXISTS idx_sub_difficulty       ON submissions(difficulty);
CREATE INDEX IF NOT EXISTS idx_sub_user_solved_date ON submissions(user_id, solved_date);
CREATE INDEX IF NOT EXISTS idx_sub_user_difficulty  ON submissions(user_id, difficulty);
CREATE INDEX IF NOT EXISTS idx_sub_user_problem     ON submissions(user_id, problem_id);

-- NOTE (correctness, not indexing): solved_date is stored as free-form TEXT in
-- DD/MM/YYYY format (see app.py's `substr(solved_date,7,4)` year-extraction).
-- BETWEEN/ORDER BY on this column sorts lexicographically, which silently
-- mis-orders across month/year boundaries. The index above will make that
-- wrong-but-fast rather than wrong-but-slow. Recommend a follow-up migration
-- that adds a real `solved_on DATE` column, backfills it by parsing the
-- existing text, and repoints queries at it — that's a data-shape change
-- with call-site updates, so it's deliberately out of scope for this
-- additive Phase 1 pass.

-- ---------------------------------------------------------------------------
-- follows
-- ---------------------------------------------------------------------------
-- PRIMARY KEY (follower_id, following_id) already indexes follower_id as its
-- leading column. following_id needs its own index for "who follows me" /
-- follower-count lookups.
CREATE INDEX IF NOT EXISTS idx_follows_following ON follows(following_id);

-- ---------------------------------------------------------------------------
-- admin_logs
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_admin_logs_admin   ON admin_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_logs_created ON admin_logs(created_at);

-- ---------------------------------------------------------------------------
-- login_history
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_login_history_user ON login_history(user_id);

-- ---------------------------------------------------------------------------
-- sync_logs
-- ---------------------------------------------------------------------------
-- app.py fetches "the most recent sync log for this user" — a composite
-- covering index makes that a single index lookup instead of a scan.
CREATE INDEX IF NOT EXISTS idx_sync_logs_user         ON sync_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_sync_logs_user_created ON sync_logs(user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- daily_challenges
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_daily_challenges_user      ON daily_challenges(user_id);
CREATE INDEX IF NOT EXISTS idx_daily_challenges_user_date ON daily_challenges(user_id, assigned_date);

-- ---------------------------------------------------------------------------
-- mentor_assignments
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_mentor_user  ON mentor_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_mentor_admin ON mentor_assignments(admin_id);

-- ---------------------------------------------------------------------------
-- custom_problems
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_custom_problems_created_by ON custom_problems(created_by);

-- ---------------------------------------------------------------------------
-- email_tokens
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON email_tokens(user_id);

-- ---------------------------------------------------------------------------
-- daily_tracker_sheet_results
-- ---------------------------------------------------------------------------
-- PRIMARY KEY (sheet_id, student_id) already covers sheet_id lookups.
-- student_id needs its own index for "all results for this student" queries.
CREATE INDEX IF NOT EXISTS idx_tracker_results_student ON daily_tracker_sheet_results(student_id);
