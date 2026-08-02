/*
config.js — the ONLY file you should need to edit when your backend
(Vercel) or frontend (Cloudflare Pages) domain changes.

PART 6 update — same-site cookie fix:
When this site is actually deployed (served from Cloudflare Pages, any
hostname other than localhost), API calls go through the SAME domain via
the "/api" proxy defined in frontend/_redirects, which Cloudflare silently
forwards to the real Vercel backend. This makes the session cookie
first-party, so it isn't blocked by Safari/Brave/strict-Firefox/etc. the
way a cross-site cookie was.

When running the frontend locally (e.g. via `python -m http.server` or
VS Code Live Server on localhost), there's no Cloudflare in front of it to
do that proxying, so we fall back to calling the Vercel backend directly —
that path still needs SESSION_COOKIE_SAMESITE=None on the backend for
local testing (see backend/.env.example), same as before.
*/
const DIRECT_BACKEND_URL = "https://daily-submission-tracker-ciyc.vercel.app";

const BASE_API_URL = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
  ? DIRECT_BACKEND_URL
  : "/api";

window.BASE_API_URL = BASE_API_URL;