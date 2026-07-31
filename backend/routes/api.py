"""
routes/api.py — placeholder.

The only JSON endpoints in the original app.py were bolted onto HTML
routes (/cookie_status, /save_settings, /follow/<id>, etc — still true
here, they just live in their feature's own route file). This file is
where the proposal's clean `/api/auth`, `/api/dashboard`, `/api/problems`,
`/api/sync`, `/api/admin`, `/api/leaderboard` endpoints would go if/when
a mobile app or external client needs a real REST layer, separate from
the server-rendered routes.
"""

from extensions import app  # noqa: F401
