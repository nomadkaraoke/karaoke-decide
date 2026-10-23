/**
 * Cloudflare Worker: API Proxy for Nomad Karaoke Decide
 *
 * Routes (decide.nomadkaraoke.com):
 *   /api/search, /api/search/*        -> search service (SEARCH_BACKEND_URL)
 *   /api/*                            -> main API (BACKEND_URL)
 *   /offline-index/<run-token>/offline.db.gz
 *                                     -> public song-index bucket, cached at the edge
 * Same-origin proxying avoids CORS for the web app.
 *
 * Environment Variables:
 *   BACKEND_URL        - main Cloud Run API URL (defaults to production)
 *   SEARCH_BACKEND_URL - search Cloud Run service URL (defaults to production)
 *
 * Deployed by Pulumi (infrastructure/__main__.py reads this file), on routes
 * decide.nomadkaraoke.com/api/* and decide.nomadkaraoke.com/offline-index/*.
 */

const DEFAULT_BACKEND_URL = "https://karaoke-decide-718638054799.us-central1.run.app";
const DEFAULT_SEARCH_BACKEND_URL = "https://karaoke-decide-search-718638054799.us-central1.run.app";

// offline.db.gz lives at <bucket>/<run>-<token>/offline.db.gz (run = YYYYMMDD-HHMMSS,
// token = 16 hex). Only that exact shape is fetched: no listing, no other objects.
const OFFLINE_INDEX_ORIGIN = "https://storage.googleapis.com/nomadkaraoke-song-index-public";
const OFFLINE_INDEX_PATH = /^\/offline-index\/([A-Za-z0-9][A-Za-z0-9-]{0,79})\/offline\.db\.gz$/;
// 7 days for hits (run dirs are immutable); never cache errors, so a run uploaded
// just after a miss isn't hidden behind a cached 403/404.
const OFFLINE_INDEX_CACHE_TTL_BY_STATUS = { "200-299": 604800, "400-599": -1 };
const OFFLINE_INDEX_HEADERS = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "etag",
  "last-modified",
  "cache-control",
];

function isSearchPath(pathname) {
  return pathname === "/api/search" || pathname.startsWith("/api/search/");
}

async function offlineIndex(request, url) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  const match = OFFLINE_INDEX_PATH.exec(url.pathname);
  if (!match || url.search) {
    return new Response("Not found", { status: 404 });
  }
  // Forward Range only, so an interrupted download can resume.
  const headers = new Headers();
  const range = request.headers.get("range");
  if (range) headers.set("range", range);
  const upstream = await fetch(`${OFFLINE_INDEX_ORIGIN}/${match[1]}/offline.db.gz`, {
    method: request.method,
    headers,
    cf: { cacheEverything: true, cacheTtlByStatus: OFFLINE_INDEX_CACHE_TTL_BY_STATUS },
  });
  if (!upstream.ok) {
    // GCS answers 403 for a missing object when listing is denied; report 404
    // and pass nothing else through.
    return new Response("Not found", { status: 404 });
  }
  const out = new Headers();
  for (const name of OFFLINE_INDEX_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/offline-index/")) {
      return offlineIndex(request, url);
    }

    // Only proxy /api/* requests
    if (!url.pathname.startsWith("/api")) {
      // Pass through to origin (GitHub Pages)
      return fetch(request);
    }

    const search = isSearchPath(url.pathname);
    const backendBaseUrl = search
      ? env.SEARCH_BACKEND_URL || DEFAULT_SEARCH_BACKEND_URL
      : env.BACKEND_URL || DEFAULT_BACKEND_URL;

    // Build the backend URL
    const backendUrl = new URL(url.pathname + url.search, backendBaseUrl);

    // Clone headers, removing Cloudflare-specific ones
    const clientIp = request.headers.get("cf-connecting-ip");
    const headers = new Headers(request.headers);
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");
    // Never trust a client-supplied value.
    headers.delete("x-nomad-client-ip");
    if (search && clientIp) {
      // The search service rate-limits per client IP (backend/search/ratelimit.py). Cloudflare
      // may overwrite CF-Connecting-IP on this subrequest to run.app, so pass a non-cf header.
      headers.set("x-nomad-client-ip", clientIp);
    }

    // Forward the request to Cloud Run
    const backendRequest = new Request(backendUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      // Pass backend 3xx through untouched: OAuth callbacks 302 to nomadkaraoke://…,
      // which a followed redirect can't fetch (and a followed web redirect would hide the URL).
      redirect: "manual",
    });

    try {
      const response = await fetch(backendRequest);

      // Clone response and add any needed headers
      const newHeaders = new Headers(response.headers);

      // Remove any existing CORS headers from backend (we don't need them now)
      newHeaders.delete("access-control-allow-origin");
      newHeaders.delete("access-control-allow-credentials");
      newHeaders.delete("access-control-allow-methods");
      newHeaders.delete("access-control-allow-headers");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders,
      });
    } catch (error) {
      // Return a proper error response
      return new Response(
        JSON.stringify({
          error: "Backend unavailable",
          message: error.message,
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
  },
};
