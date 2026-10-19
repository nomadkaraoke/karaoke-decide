// Tests for api-proxy.js routing. Run: node --test infrastructure/cloudflare-worker/
// (tests/unit/test_api_proxy_worker.py runs this under pytest).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";

const src = readFileSync(new URL("./api-proxy.js", import.meta.url), "utf8");
const worker = (await import(`data:text/javascript,${encodeURIComponent(src)}`)).default;

let calls;
const realFetch = globalThis.fetch;

beforeEach(() => {
  calls = [];
  globalThis.fetch = async (input, init) => {
    const req = input instanceof Request ? input : null;
    calls.push({
      url: req ? req.url : String(input),
      method: req ? req.method : (init?.method ?? "GET"),
      headers: req ? req.headers : new Headers(init?.headers),
      cf: init?.cf,
    });
    return new Response("body", {
      status: 200,
      headers: {
        "content-type": "application/gzip",
        "x-goog-generation": "1",
        "access-control-allow-origin": "*",
      },
    });
  };
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

const ENV = {
  BACKEND_URL: "https://main.example",
  SEARCH_BACKEND_URL: "https://search.example",
};

function get(path, headers = {}, method = "GET") {
  return worker.fetch(new Request(`https://decide.nomadkaraoke.com${path}`, { method, headers }), ENV, {});
}

test("/api/search and /api/search/* go to the search service", async () => {
  await get("/api/search?q=abba");
  await get("/api/search/offline-index");
  assert.equal(calls[0].url, "https://search.example/api/search?q=abba");
  assert.equal(calls[1].url, "https://search.example/api/search/offline-index");
});

test("other /api paths (including look-alikes) go to the main API", async () => {
  await get("/api/health");
  await get("/api/searches");
  assert.equal(calls[0].url, "https://main.example/api/health");
  assert.equal(calls[1].url, "https://main.example/api/searches");
});

test("defaults apply without env bindings", async () => {
  await worker.fetch(new Request("https://decide.nomadkaraoke.com/api/search/health"), {}, {});
  assert.equal(calls[0].url, "https://karaoke-decide-search-718638054799.us-central1.run.app/api/search/health");
});

test("search requests keep the client IP for rate limiting; main API requests drop it", async () => {
  await get("/api/search?q=x", { "cf-connecting-ip": "203.0.113.9", "cf-ray": "r" });
  await get("/api/health", { "cf-connecting-ip": "203.0.113.9" });
  assert.equal(calls[0].headers.get("cf-connecting-ip"), "203.0.113.9");
  assert.equal(calls[0].headers.get("cf-ray"), null);
  assert.equal(calls[1].headers.get("cf-connecting-ip"), null);
});

test("proxied responses lose backend CORS headers", async () => {
  const res = await get("/api/search?q=x");
  assert.equal(res.headers.get("access-control-allow-origin"), null);
});

test("offline index passthrough fetches the public bucket object with edge caching", async () => {
  const res = await get("/offline-index/20261018-133000-0123456789abcdef/offline.db.gz");
  assert.equal(res.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://storage.googleapis.com/nomadkaraoke-song-index-public/20261018-133000-0123456789abcdef/offline.db.gz",
  );
  assert.deepEqual(calls[0].cf, { cacheEverything: true, cacheTtlByStatus: { "200-299": 604800, "400-599": -1 } });
  assert.equal(calls[0].headers.get("range"), null);
  assert.equal(res.headers.get("content-type"), "application/gzip");
  assert.equal(res.headers.get("x-goog-generation"), null); // only an allowlist passes
});

test("offline index forwards Range (and nothing else) and passes a 206 through", async () => {
  globalThis.fetch = async (input, init) => {
    calls.push({ url: String(input), headers: new Headers(init?.headers) });
    return new Response("partial", {
      status: 206,
      headers: { "content-range": "bytes 100-106/1000", "accept-ranges": "bytes", "x-goog-hash": "h" },
    });
  };
  const res = await get("/offline-index/r-1/offline.db.gz", { range: "bytes=100-", cookie: "c=1" });
  assert.equal(calls[0].headers.get("range"), "bytes=100-");
  assert.equal(calls[0].headers.get("cookie"), null);
  assert.equal(res.status, 206);
  assert.equal(res.headers.get("content-range"), "bytes 100-106/1000");
  assert.equal(res.headers.get("accept-ranges"), "bytes");
  assert.equal(res.headers.get("x-goog-hash"), null);
});

test("offline index HEAD is allowed, other methods are not", async () => {
  assert.equal((await get("/offline-index/r-1/offline.db.gz", {}, "HEAD")).status, 200);
  assert.equal((await get("/offline-index/r-1/offline.db.gz", {}, "POST")).status, 405);
  assert.equal(calls.length, 1);
});

for (const path of [
  "/offline-index/",
  "/offline-index/run/",
  "/offline-index/run/song_id.db",
  "/offline-index/run/offline.db",
  "/offline-index/a/b/offline.db.gz",
  "/offline-index/run.x/offline.db.gz",
  "/offline-index/-run/offline.db.gz",
  "/offline-index/run/offline.db.gz?list=1",
  `/offline-index/${"a".repeat(81)}/offline.db.gz`,
]) {
  test(`offline index rejects ${path.slice(0, 60)}`, async () => {
    const res = await get(path);
    assert.equal(res.status, 404);
    assert.equal(calls.length, 0);
  });
}

test("dot segments are resolved away by URL parsing and never reach the bucket", async () => {
  await get("/offline-index/../offline.db.gz");
  await get("/offline-index/%2e%2e/offline.db.gz");
  assert.ok(calls.every((c) => !c.url.includes("storage.googleapis.com")));
});

test("offline index upstream errors become a bare 404", async () => {
  globalThis.fetch = async () => new Response("<Error>AccessDenied</Error>", { status: 403 });
  const res = await get("/offline-index/r-1/offline.db.gz");
  assert.equal(res.status, 404);
  assert.equal(await res.text(), "Not found");
});
