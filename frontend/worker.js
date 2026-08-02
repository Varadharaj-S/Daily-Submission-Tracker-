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
};
