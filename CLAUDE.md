# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DSA Tracker — a multi-platform competitive programming submission tracker that syncs solved problems from LeetCode, Codeforces, and AtCoder into a PostgreSQL database and per-user Google Sheets. Includes a Chrome extension for LeetCode cookie capture, daily auto-sync via cron, leaderboards, mentor-assigned challenges, weekly reports, and contest tracking.

## Architecture

**Monorepo with two deployable units:**

- `backend/` — Python/Flask API, deployed to **Vercel** (serverless) via `vercel.json`. Also deployable to **Render** via `render.yaml` + Procfile (gunicorn).
- `frontend/` — Static HTML/CSS/JS site, deployed to **Cloudflare Workers** via `wrangler.jsonc`. A `worker.js` proxies `/api/*` to the Vercel backend so the session cookie stays first-party.

**Key backend design decisions:**

- No Flask Blueprints — all route modules import the shared `app` object from `extensions.py` and use `@app.route(...)` directly. This preserves endpoint names for `url_for()` across Python and Jinja templates.
- No ORM — raw SQL via `psycopg2` through `db.py`'s `get_db()` context manager. The `Cursor` wrapper converts `?` placeholders to `%s` for sqlite→postgres compatibility.
- `database/models.py` is a schema reference (dataclasses), not an ORM layer.
- `services/sync_engine.py` is the single import point for sync/import functions — all route modules import from there, never directly from `bot.py` or `normal_sync.py`.

**Import order constraint:** `extensions.py` must not import from `routes/` or `services/` (circular import). `app.py` imports `extensions` first, then route modules for side-effect registration.

## Development Commands

```bash
# Backend local dev (from backend/ directory)
pip install -r requirements.txt
python app.py                    # runs Flask dev server with auto-reload + sync worker thread

# Production server
gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT --timeout 120

# Frontend local dev
cd frontend
npx wrangler dev                 # or serve with any static file server (e.g. python -m http.server)

# Daily sync (what the Render cron job runs)
python scheduler.py

# Database migrations (one-off)
python database/migrate.py
```

## Required Environment Variables

Backend requires at minimum:
- `DATABASE_URL` — PostgreSQL connection string (e.g. `postgresql://user:pass@host:5432/dsa_tracker`)
- `GOOGLE_SERVICE_JSON` — Google service account JSON string (for Sheets sync)
- `SECRET_KEY` — Flask session secret (random fallback generated if unset, but sessions won't survive restarts)

Optional: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `FROM_EMAIL` (email verification), `FRONTEND_ORIGINS` (CORS), `SESSION_COOKIE_SAMESITE`, `IDLE_TIMEOUT_MINUTES`.

## Backend Module Map

| Path | Role |
|------|------|
| `app.py` | Entry point — inits DB, registers all route modules, error handlers |
| `extensions.py` | Shared Flask `app` instance, login manager, CORS, request hooks |
| `config.py` | All config from env vars |
| `db.py` / `database/db.py` | PostgreSQL connection, `get_db()`, schema init functions |
| `routes/` | One file per feature area (auth, dashboard, sync, tracker, contest, etc.) |
| `services/` | Business logic — sync_engine, auth_service, tracker_service, incremental_sync |
| `sync/` | Platform-specific fetchers (cf_service, lc_service, ac_service) |
| `contest/` | Contest tracker system (API, service, sync, Google Sheets integration) |
| `workers/` | Background threads (sync_worker uses threading, not Celery) |
| `bot.py` | LeetCode GraphQL fetcher, dashboard data generation, full LC import |
| `normal_sync.py` | Production sync path — syncs all platforms + writes to Google Sheets |
| `scheduler.py` | Standalone cron script for daily sync (Render Cron entry point) |

## Frontend

Plain HTML pages with vanilla JS. No build step, no framework. `config.js` sets `window.BASE_API_URL` — uses `/api` prefix in production (proxied by Cloudflare Worker) or the direct Vercel URL on localhost. Shared utilities in `assets/js/app.js`.

## Sync Flow

1. Chrome extension captures LeetCode session cookie → `POST /save_cookie` stores it in `users` table
2. Daily cron (`scheduler.py` or Vercel cron at `/api/cron/daily-sync`) iterates all verified users
3. Per user: fetches submissions from CF/LC/AC APIs, deduplicates against `submissions` table, writes new rows to DB + user's Google Sheet tab
4. `services/incremental_sync/` is a newer implementation (built, tested, NOT yet wired in) — swap-over is a one-line change in `sync_engine.py`

## Deployment

- **Vercel**: `vercel.json` routes everything to `backend/app.py`. Cron jobs defined for daily-sync and contest-sync.
- **Render**: `render.yaml` defines web service + two cron jobs (daily sync at 02:00 UTC, contest sync every 5 min) + managed PostgreSQL.
- **Cloudflare Workers**: `frontend/wrangler.jsonc` — serves static assets + proxies `/api/*` to Vercel backend.

Schema migrations run automatically on cold start via `init_db()`, `ensure_extension_schema()`, `ensure_contest_schema()`, and `ensure_tracker_schema()`.
