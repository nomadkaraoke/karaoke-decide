/**
 * Cloudflare Worker: API Proxy for Nomad Karaoke Decide
 *
 * Proxies requests from decide.nomadkaraoke.com/api/* to Cloud Run backend.
 * This eliminates CORS issues by keeping everything same-origin.
 *
 * Environment Variables:
 *   BACKEND_URL - Cloud Run backend URL (defaults to production)
 *
 * Setup:
 * 1. Create a new Worker in Cloudflare dashboard
 * 2. Paste this code
 * 3. Add route: decide.nomadkaraoke.com/api/*
 * 4. (Optional) Set BACKEND_URL environment variable for non-production
 */

const DEFAULT_BACKEND_URL = "https://karaoke-decide-718638054799.us-central1.run.app";

export default {
  async fetch(request, env, ctx) {
    const backendBaseUrl = env.BACKEND_URL || DEFAULT_BACKEND_URL;
    const url = new URL(request.url);

    // Only proxy /api/* requests
    if (!url.pathname.startsWith("/api")) {
      // Pass through to origin (GitHub Pages)
      return fetch(request);
    }

    // Build the backend URL
    const backendUrl = new URL(url.pathname + url.search, backendBaseUrl);

    // Clone headers, removing Cloudflare-specific ones
    const headers = new Headers(request.headers);
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");

    // Forward the request to Cloud Run
    const backendRequest = new Request(backendUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow",
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
