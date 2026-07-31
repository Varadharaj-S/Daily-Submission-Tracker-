"""
database/models.py — schema reference for DSA Tracker v4.

This is intentionally NOT an ORM layer. The app talks to Postgres through
db.get_db() with raw SQL (via the sqlite-compatible Cursor/Connection wrapper
in db.py), and Phase 1 keeps that pattern rather than forcing a rewrite onto
an ORM at the same time as a schema migration. This module exists so that
Phase 2 (splitting app.py into routes/services) has one place to look up
"what columns does this table actually have" instead of grepping CREATE
TABLE statements scattered across db.py and app.py.

Each entry mirrors the live schema after database/migrate.py has been run.
If you add a column or table, update it here in the same PR.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class User:
    id: int
    username: str
    email: str = ""
    password: str = ""
    is_admin: int = 0
    is_verified: int = 0
    status: str = "pending"                 # 'pending' | 'active' | 'banned' (as used by app.py)
    sheet_id: str = ""
    cf_handle: str = ""
    lc_handle: str = ""
    lc_password: str = ""
    lc_session_cookie: str = ""
    lc_csrf_token: str = ""
    cookie_expiry: int = 0
    ac_handle: str = ""
    enabled_platforms: str = '["Codeforces","LeetCode","AtCoder"]'  # JSON-encoded list, stored as TEXT
    bio: str = ""
    is_public: int = 1
    last_login: str = ""                    # ISO-format TEXT, not a real TIMESTAMP column
    last_sync: str = ""
    created_at: str = ""
    lc_imported: int = 0
    auto_sync_enabled: int = 1
    sync_time: str = "09:00"


@dataclass
class Submission:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    platform: str
    problem_name: Optional[str] = None
    problem_id: Optional[str] = None
    problem_url: str = ""
    difficulty: str = ""
    tags: str = ""
    solved_date: str = ""                   # TEXT in DD/MM/YYYY — see indexes.sql note.
    # UNIQUE(user_id, platform, problem_id)


@dataclass
class Follow:
    follower_id: int                        # FK -> users.id, ON DELETE CASCADE
    following_id: int                       # FK -> users.id, ON DELETE CASCADE
    created_at: str = ""
    # PRIMARY KEY (follower_id, following_id)


@dataclass
class AdminLog:
    id: int
    admin_id: Optional[int] = None          # FK -> users.id, ON DELETE SET NULL
    action: str = ""
    target_user: str = ""
    details: str = ""
    ip: str = ""
    created_at: str = ""


@dataclass
class LoginHistory:
    id: int
    user_id: Optional[int] = None           # FK -> users.id, ON DELETE CASCADE
    ip: str = ""
    user_agent: str = ""
    success: int = 0
    created_at: str = ""


@dataclass
class SyncLog:
    id: int
    user_id: Optional[int] = None           # FK -> users.id, ON DELETE CASCADE
    status: str = ""
    message: str = ""
    created_at: str = ""


@dataclass
class DailyChallenge:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    problem_name: str = ""
    problem_url: str = ""
    difficulty: str = ""
    platform: str = ""
    topic: str = ""
    assigned_date: str = ""
    completed: int = 0
    assigned_by: Optional[int] = None       # FK -> users.id, ON DELETE SET NULL (NULL = system-assigned)
    # UNIQUE(user_id, problem_url, assigned_date)


@dataclass
class MentorAssignment:
    id: int
    admin_id: Optional[int] = None          # FK -> users.id, ON DELETE SET NULL
    user_id: int = 0                        # FK -> users.id, ON DELETE CASCADE
    problem_name: str = ""
    problem_url: str = ""
    difficulty: str = ""
    platform: str = ""
    topic: str = ""
    note: str = ""
    assigned_date: str = ""
    due_date: str = ""
    completed: int = 0


@dataclass
class CustomProblem:
    id: int
    created_by: Optional[int] = None        # FK -> users.id, ON DELETE SET NULL
    title: str = ""
    description: str = ""
    difficulty: str = ""
    topic: str = ""
    url: str = ""
    created_at: str = ""


@dataclass
class EmailToken:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    token: str = ""
    created_at: str = ""
    used: int = 0


@dataclass
class DailyTrackerSheet:
    id: int
    name: str = ""
    sheet_type: str = "custom"
    description: str = ""
    target_value: int = 0
    target_topic: str = ""
    created_by: Optional[int] = None        # FK -> users.id, ON DELETE SET NULL (NULL = system-seeded)
    visibility: str = "private"
    created_at: str = ""


@dataclass
class DailyTrackerSheetResult:
    sheet_id: int                           # FK -> daily_tracker_sheets.id, ON DELETE CASCADE
    student_id: int                         # FK -> users.id, ON DELETE CASCADE
    status: str = ""
    solved_count: int = 0
    details_json: str = ""
    remarks: str = ""
    updated_at: str = ""
    # PRIMARY KEY (sheet_id, student_id)


# --- New in Phase 1 — inert until Phase 3 wires up the services that use them ---

@dataclass
class DashboardCache:
    user_id: int                            # PK, FK -> users.id, ON DELETE CASCADE
    payload: dict = field(default_factory=dict)   # JSONB
    computed_at: str = ""                   # TIMESTAMPTZ


@dataclass
class LeaderboardCache:
    scope: str = "global"                   # PK — e.g. 'global', 'weekly_2026_W28'
    payload: list = field(default_factory=list)   # JSONB
    computed_at: str = ""                   # TIMESTAMPTZ


@dataclass
class WeeklyReport:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    week_start: str = ""                    # DATE
    week_end: str = ""                      # DATE
    payload: dict = field(default_factory=dict)   # JSONB
    created_at: str = ""                    # TIMESTAMPTZ
    # UNIQUE(user_id, week_start)


@dataclass
class Notification:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    type: str = ""
    title: str = ""
    body: str = ""
    link: str = ""
    is_read: bool = False
    created_at: str = ""                    # TIMESTAMPTZ


@dataclass
class SheetBackup:
    id: int
    user_id: int                            # FK -> users.id, ON DELETE CASCADE
    sheet_id: str = ""
    backup_path: str = ""
    row_count: int = 0
    created_at: str = ""                    # TIMESTAMPTZ


@dataclass
class ActivityLog:
    id: int
    user_id: Optional[int] = None           # FK -> users.id, ON DELETE SET NULL
    action: str = ""
    metadata: dict = field(default_factory=dict)  # JSONB
    ip: str = ""
    created_at: str = ""                    # TIMESTAMPTZ


@dataclass
class Session:
    id: str                                 # PK — session token / uuid
    user_id: int = 0                        # FK -> users.id, ON DELETE CASCADE
    user_agent: str = ""
    ip: str = ""
    created_at: str = ""                    # TIMESTAMPTZ
    expires_at: str = ""                    # TIMESTAMPTZ
    revoked: bool = False
