/**
 * worker.js — Cloudflare Worker for DSA Tracker frontend.
 *
 * Does two things:
 *   1. Serves static assets (HTML/CSS/JS files in the assets directory)
 *      via the built-in Assets binding.
 *   2. Proxies every /api/* request to the Vercel backend — transparently,
 *      so the browser sees one domain and the session cookie is first-party.
 *
 * This replaces the _redirects approach (which only works on Cloudflare Pages,
 * not Workers). The proxy behavior is identical: /api/login → backend /login,
 * /api/auth/me → backend /api/auth/me, etc.
 */

const BACKEND = "https://daily-submission-tracker-ciyc.vercel.app";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ── Proxy /api/* to Vercel backend ────────────────────────────────────
    if (url.pathname.startsWith("/api/")) {
      // Strip the leading /api prefix → /api/login becomes /login,
      // /api/auth/me becomes /api/auth/me (backend already has /api/ prefix
      // on that specific route).
      const backendPath = url.pathname.slice(4); // removes "/api"
      const backendURL = BACKEND + backendPath + url.search;

      // Clone headers and forward everything including cookies
      const proxyHeaders = new Headers(request.headers);
      // Set Host to the backend so Vercel accepts the request
      proxyHeaders.set("Host", new URL(BACKEND).hostname);
      // Tell backend the real frontend origin for CORS
      proxyHeaders.set("X-Forwarded-Host", url.hostname);
      proxyHeaders.set("X-Forwarded-Proto", "https");

      const proxyRequest = new Request(backendURL, {
        method: request.method,
        headers: proxyHeaders,
        body: request.method !== "GET" && request.method !== "HEAD"
          ? request.body
          : null,
        redirect: "follow",
      });

      const backendResponse = await fetch(proxyRequest);

      // Forward the response — including Set-Cookie headers so the session
      // cookie is set on the frontend domain (making it first-party).
      const responseHeaders = new Headers(backendResponse.headers);
      // Remove any backend CORS headers — browser sees only one domain now
      responseHeaders.delete("Access-Control-Allow-Origin");
      responseHeaders.delete("Access-Control-Allow-Credentials");

      return new Response(backendResponse.body, {
        status: backendResponse.status,
        statusText: backendResponse.statusText,
        headers: responseHeaders,
      });
    }

    // ── Serve static assets ────────────────────────────────────────────────
    // Fall through to the Workers Assets binding (configured in wrangler.jsonc)
    return env.ASSETS.fetch(request);
  },

  /**
   * scheduled() — Cloudflare Cron Trigger handler. This is the ACTUAL daily
   * auto-sync driver for this deployment (Cloudflare frontend + Vercel
   * backend, no Render worker).
   *
   * Why this exists: a Vercel serverless function is hard-capped at ~10s
   * (Hobby) / 60s (Pro) per invocation — nowhere near enough to sync
   * hundreds or thousands of users in one request. So the backend's
   * /api/cron/daily-sync only does ONE small batch (backend/scheduler.py's
   * sync_batch(), default 15 users) per call and returns a cursor. This
   * Worker isn't bound by that limit (it's just making outbound fetch()
   * calls and waiting — no heavy CPU), so it loops the batch endpoint
   * itself until every active user is synced for the day, then stops.
   *
   * Requires a CRON_SECRET secret set on this Worker (matches the
   * backend's CRON_SECRET env var) — set it with:
   *   npx wrangler secret put CRON_SECRET
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runDailySyncLoop(env));
  },
};

async function runDailySyncLoop(env) {
  const secret = env.CRON_SECRET;
  const headers = secret ? { Authorization: `Bearer ${secret}` } : {};

  let afterId = 0;
  let done = false;
  let loops = 0;
  let totalOk = 0;
  let totalFail = 0;
  let totalExpired = 0;

  // Safety cap so a stuck backend can't loop forever and burn Worker CPU
  // quota — 200 loops * 15 users/batch = 3000 users, comfortably above the
  // ~1000-user scale this was built for.
  const MAX_LOOPS = 200;

  while (!done && loops < MAX_LOOPS) {
    loops += 1;
    const resp = await fetch(
      `${BACKEND}/api/cron/daily-sync?after_id=${afterId}`,
      { method: "POST", headers }
    );

    if (!resp.ok) {
      console.error(`[daily-sync] batch request failed: HTTP ${resp.status}`);
      break;
    }

    const data = await resp.json();
    if (!data.ok) {
      console.error(`[daily-sync] batch reported failure: ${data.message || "unknown"}`);
      break;
    }

    afterId = data.next_cursor;
    done = data.done;
    totalOk += data.ok || 0;
    totalFail += data.fail || 0;
    totalExpired += data.cookie_expired || 0;

    console.log(
      `[daily-sync] batch ${loops}: processed=${data.processed} ok=${data.ok} ` +
      `fail=${data.fail} expired=${data.cookie_expired} next_cursor=${afterId} done=${done}`
    );

    // Small pause between batches — polite to the CF/LC/AC APIs and to
    // Neon's connection pool, and keeps this well under Cloudflare's own
    // subrequest-per-invocation limits.
    if (!done) {
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  console.log(
    `[daily-sync] finished: ${loops} batch(es), ok=${totalOk} fail=${totalFail} ` +
    `expired=${totalExpired}${loops >= MAX_LOOPS ? " (hit MAX_LOOPS safety cap)" : ""}`
  );
}
