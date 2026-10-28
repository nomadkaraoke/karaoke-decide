# API Reference

Base URL: `https://api.decide.nomadkaraoke.com` (production)
Local: `http://localhost:8000`

## Authentication

Most endpoints require a Bearer token in the Authorization header:

```
Authorization: Bearer <token>
```

Two token types are accepted:

- **Gen session tokens** (dot-less): validated against gen's `sessions` collection (same Firestore project) with gen's rules (exists, not revoked, not expired, `last_activity_at` within 7 days, user exists and enabled) plus one decide-only rule: tenant sessions (`tenant_id` set) are rejected. Results are cached in-process (60s valid, 10s invalid) and `last_activity_at` is slid at most hourly. Failures return `401` with `detail` = `gen_session_<reason>`, where reason is `invalid`, `revoked`, `expired`, `idle`, `tenant`, `user_not_found`, `user_disabled` or `error`. The decide profile (`decide_users/{sha256(email)}`) is created on the first request if absent.
- **Legacy decide JWTs** (contain dots): accepted through 2026-11-30 inclusive; afterwards `401` with `detail: "legacy_token_expired"`.

---

## Health

### GET /api/health

Health check endpoint for load balancers and monitoring.

**Response:**
```json
{
  "status": "healthy",
  "service": "karaoke-decide"
}
```

### GET /api/health/deep

Deep health check that validates connectivity to all infrastructure components.
Useful for post-deploy verification and scheduled monitoring.

**Response:**
```json
{
  "status": "healthy",
  "service": "karaoke-decide",
  "timestamp": "2025-12-31T12:00:00Z",
  "checks": {
    "firestore": {
      "status": "healthy",
      "message": "Connected, 42 users in database"
    },
    "bigquery": {
      "status": "healthy",
      "message": "Connected, 275,809 songs in catalog"
    },
    "cloud_tasks": {
      "status": "healthy",
      "message": "Queue 'projects/.../queues/sync-queue' accessible"
    }
  }
}
```

**Status values:**
- `healthy` - All checks passed
- `degraded` - One or more checks failed (see individual check status)

---

### GET /api/ready

`GET /api/ready` is the startup probe: `{"ok": true, "catalog": bool, "availability": bool, "run": str | null}`. `catalog: false` means recommendations v2 is still warming up; everything else serves.

### GET /api/my/recommendations/v2

Catalog-backed, explained recommendations (auth: decide JWT or gen session token). The web recommendations page uses this endpoint (all filters, sort and library). Query params (all optional):
`genre` (multi), `mood` (multi), `energy` (low|mid|high), `decade` (multi), `popularity` (min 0-100), `popularity_max` (0-100), `lead_gender` (male|female|mixed), `duration` (short|medium|long), `classic` (bool), `explicit` (exclude|include), `has_karaoke` (yes|make|any), `library` (any|community|karafun), `singable` (bool), `sort` (best|popular|newest|oldest|shortest|az|fit), `cursor`.

`sort=best` returns sections in order `go_tos`, `songs_you_know`, `artists_you_love`, `because_<artist_key>` (max 2), `crowd_pleasers`, `make_it` (empty sections omitted; at most 3 songs per artist per section; hidden songs never appear). Any other sort returns one flat section `all`, paged by `next_cursor`.

```json
{"sections": [{"id": "crowd_pleasers", "title_key": "...", "reason_key": "...", "title_params": {}, "songs": [{"song_key": "artist|title", "artist": "...", "title": "...", "reason": "...", "reason_type": "...", "reason_artist": null, "score": 0.8, "has_karaoke_version": true, "karaoke": {}, "facets": {"decade": "1990s", "genres": ["rock"], "mood": "happy", "energy": 0.7, "lead_gender": "male", "duration_ms": 215000, "popularity": 80, "is_classic": false, "explicit": false}, "fit": {"bucket": "fitsIn", "transpose": -2, "octave": 0, "tier": "notes"}, "crowd": null}]}],
 "filters_applied": {}, "next_cursor": null, "has_profile": true, "catalog_run": "20261021-120000", "voice": {"has_profile": true, "method": "mic"}}
```

Vocal fit (needs a voice profile, `PUT /api/my/voice`): each song's `fit` is `{bucket: fits|fitsIn|stretch|outside, transpose, octave, tier}` (`fitsIn` = sing it `transpose` semitones / `octave` shifted), or `null` without a profile or without a song vocal range. `best` adds `0.15 x` fit affinity (fits 1.0, fitsIn 0.7, stretch 0.2, outside -0.5, unknown 0). `singable=true` keeps fits/fitsIn only (songs without a range drop out); without a profile it is ignored and not echoed in `filters_applied`. `sort=fit` is the flat `all` list ordered by fit bucket (fits, fitsIn, stretch, outside), then fit score, then popularity; songs without a range sort last. Once `catalog.db` has the `vr_*` columns the SQL prefilter keeps only songs with a range; before the catalog rebuild (schema 1) it returns songs with `fit: null` in popularity order. Without a profile it falls back to popularity order. `has_profile` is the taste profile; `voice.has_profile` is the voice profile.

Errors: `400 bad_cursor`; `503 warming_up` (with `Retry-After: 30`) until `catalog.db` has loaded. `GET /api/my/recommendations/categorized` (v1) is unchanged and keeps working.

Sung history and surveys (Phase 4b): the karaoke log's per-song state on `user_songs` (`times_sung`, `sung_feel_latest`, `sung_key_latest`) feeds the weights. A sung song weighs 1.0 when its latest feel is `great` or it was sung 2+ times with a latest feel that isn't `bad`, 0.8 when sung once with no feel, 0.7 when the latest feel is `ok`; a song's weight is the max of its source, voice-session and sung weights. A latest feel of `bad` puts the song in `survey_no`: it scores -0.4, is never "known", never seeds a `because_*` shelf and doesn't count toward its artist. The user's own latest key answer is a key hint (`too_high` shifts the in-key fit -2, `too_low` +2).

- **`go_tos`** (first, limit 10): the user's go-to karaoke songs, i.e. not hidden, latest feel not `bad`, and either latest feel `great` or `times_sung >= 2` (the same rule as the app's `isGoTo`). Go-tos past the shelf's limit or per-artist cap fall through to `songs_you_know`; a go-to never appears in both. Title/subtitle keys `recs.section.go_tos.title` / `.reason` (no params).
- **`reason_type: "sung_before"`** ("You've sung this"): every go-to's reason; it takes precedence over `known_song`. Web key `recs.reason.sung_before`.

Crowd: each song carries `crowd` (the `SongCrowd` figure below, from one batch read of `song_crowd_stats` for the page) or `null` when the song has no stats or the read failed (logged at WARNING; the page still returns). Within `crowd_pleasers` only, songs whose `crowd_pleaser_share >= 0.5` move to the front (stable). When at least 60% of 5+ singers in the viewer's voice bucket found a song too high (or too low), its in-key fit shifts -2 (or +2) like the user's own key hint; any key answer of the user's own ("right" included) always wins.

`RecSong.crowd` is the same figure as the route below (same thresholds), attached per song.

### GET /api/songs/{song_key}/crowd

How other singers found a song's key (auth required; `song_key` percent-encoded). One vote per singer per song: the singer's latest key answer (`too_low|right|too_high`) across their karaoke-log records for it, counted in `total` and, when their voice profile has a voice type, in `by_bucket.<bass|baritone|tenor|alto|mezzo|soprano>` (a register-only or missing profile votes in `total` only); the crowd-pleaser figure likewise uses each singer's latest answer. Thresholds: `CROWD_MIN_SINGERS = 5` overall, `CROWD_MIN_BUCKET = 5` for the bucket figure. Returns `{singers, comfortable_share, too_high_share, too_low_share, bucket, bucket_singers, bucket_comfortable_share, crowd_pleaser_share}`. Thresholds are applied server-side: the overall fields are `null` below 5 key answers; the bucket fields are `null` unless the viewer's voice type (bass ... soprano) has 5+ answers; `crowd_pleaser_share` is `null` below 5 crowd-pleaser answers. An unknown song returns all `null`. Stats docs live at `song_crowd_stats/{crowd_doc_id(song_key)}` (sha1 hex, 32 chars) with the `song_key` as a field.

### Get this made: /api/my/get-made

The user's songs with no karaoke version yet (Me tab "Get this made"). Bearer gen session token.

| Route | Purpose |
|-------|---------|
| `GET /api/my/get-made` | `{items: [{key, song_key, artist, title, why: saved\|sung\|imported\|voice\|recommended, weight}]}`, at most 30, **in server order**. |
| `POST /api/my/get-made/{key}/dismiss` | 204. Hides `key` from this list only (never from recs). Idempotent. |
| `DELETE /api/my/get-made/{key}/dismiss` | 204. Restores it. Idempotent. |

- Candidates are the user's `user_songs` rows with no karaoke version that are saved (loved), sung, imported (a listening source) or voice-session "yes", plus uncatalogued songs they sang (`user_karaoke_unresolved`). The recs v2 `make_it` shelf tops the list up to 30 as `why: recommended`.
- Excluded: dismissed keys, songs whose latest feel is `bad`, voice-session "no" songs and hidden songs.
- `key` is the library identity: the catalog `song_key` when the song is in catalog.db (then `song_key == key`), else `"u:" + song_key(artist, title)` with `song_key: null`. Percent-encode it in the dismiss path (it holds `/`, `|`, `#`); keys over 512 characters are 400 `invalid`.
- `why` is the strongest reason a row supports (saved > sung > voice > imported); `weight` is the recs weight of that reason. **`weight` is only comparable within one `why` group**: a `recommended` item's `weight` is its recs score, on a different scale from the user-song weights. Clients must keep the server's order and not re-sort by `weight`.
- Dismissals live in `user_make_dismissed/{user_id}:{sha1(key)[:24]}` (per user, deleted with the account).
- Errors: `401` without a token; `503 warming_up` (with `Retry-After: 30`) until `catalog.db` has loaded.

### Song feedback: /api/my/songs

`GET /api/my/songs/hidden`, `GET /api/my/songs/loved` list `{songs: [...]}`. `POST` / `DELETE /api/my/songs/{song_key}/hide` and `/love` (204) hide or love a song; hidden songs never appear in v2.

Love marks the song's `user_songs` row (`loved: true`, `loved_at`) with a merge: the `{user_id}:{kn_id}` row when catalog.db maps the key to a KaraokeNerds id and that row exists, else the song_key row (created with `source: manual`). It is the same row the karaoke log counts into, so `times_sung`, `last_sung_at` and the row's source survive. Unlove clears the marker and deletes the row only when nothing else is on it (no `times_sung`, source `manual` or `karaoke_log`). Rows written before the marker existed count as loved when their source is `manual`.

### Karaoke log: /api/my/karaoke-log

Every song the user sang (or planned / requested), from kjbox at Nomad nights (`kj_…` ids) or the mobile app at other bars (`app_…` ids), with the optional post-song survey. Bearer gen session token. Errors are `{"detail": "<code>"}`: `invalid` (400), `not_owner` (403), `not_found` (404); a body that is not a JSON object, or `limit` outside 1–500, is 422.

| Route | Purpose |
|-------|---------|
| `GET /api/my/karaoke-log?since=&night_id=&song=&limit=100&cursor=` | `{entries: [entry], next_cursor}`, newest `updated_at` first. `song=<libraryKey>` keeps only that song's records (the key from `library_key()`: the resolved `song_key`, else the normalised artist + title). Without `since`: live records. With `since` (ISO): every record updated after it, deleted ones included (sync pull). Survey-only placeholders are never listed. |
| `PUT /api/my/karaoke-log/{id}` | Upsert with field-level merge → the entry. `app_` ids: any of `state, artist, title, song_key, night, sung_at, ended_at, media, survey, origin, times, deleted` (a new entry needs `state`, `artist`, `title`). `kj_` ids: only `survey` and `deleted` (other kjbox fields → 403 `not_owner`); a survey written before kjbox delivers is kept and merged later. `deleted: false` never revives a record the host un-did. |
| `DELETE /api/my/karaoke-log/{id}` | Soft delete → the entry (`deleted: true`); reverses its sung count. |

Entry: `{id, source: kjbox|app, state: planned|requested|sung|sung_unplayed|skipped, artist, title, song_key, song_key_status: resolved|unresolved, night: {id, kind: nomad|other, venue_name, date, date_precision: day|month|year|unknown}, sung_at, ended_at, created_at, updated_at, origin: live|backfill, times (1–50), survey: {feel: great|ok|bad, key: too_low|right|too_high, crowd_pleaser, vibes: [party|ballad|warm_up|duet|emotional|rock_out|singalong] (≤7), reasons: [out_of_range|too_hard|crowd_quiet|awkward_music|too_long|didnt_know_it] (≤6), notes (≤2000), answered_at}, media: {brand_code, gen_job_id, source_type}, deleted, box_id, entry_id, singers}`.

Counts: a `sung`/`sung_unplayed` record that isn't deleted adds `times` (default 1; backfill records say "sung it 5 times" with `origin: backfill`) to `user_songs.times_sung` (same row rule as Love) when `song_key` is resolved, else to `user_karaoke_unresolved` (per normalised artist + title). `song_key` is resolved server-side with a 300 ms budget; unresolved records are retried in the background at most once per 24 h per user, on `GET` (marker `karaoke_log_retry_at` on the `decide_users` doc).

### Library: GET /api/my/library

The Me tab's collection in one call: every song the user has sung, loved, hidden or put on a setlist, aggregated from the karaoke log, `user_songs` and playlists. Bearer gen session token.

`{items: [item], etag}`. Item: `{key, song_key, artist, title, times_sung, backfill_times, first_sung_at, last_sung_at, feel_counts: {great, ok, bad}, latest_feel, latest_key, crowd_pleaser_count, vibes, loved, hidden, setlist_ids, facets}`.

- `key` is the library key: the resolved `song_key`, else the normalised artist + title (the value for `karaoke-log?song=`). `song_key` is null for unresolved songs.
- `latest_feel` and `latest_key` are the newest answer per field (feel and key are tracked separately). `backfill_times` is the part of `times_sung` that came from backfill records.
- `facets`: `{mood, energy_band, has_karaoke, community, karafun, vr_tier}` from catalog.db; null when the song is unresolved or the catalog is unavailable.
- The response carries `ETag`; a matching `If-None-Match` gets **304** with no body.

### kjbox partner: /api/partner/kjbox/karaoke-log

Server to server from kjbox's history outbox. Header `X-Kjbox-Secret` must equal `DECIDE_KJBOX_SECRET` (Secret Manager `decide-kjbox-secret`; compared with `hmac.compare_digest`): missing or wrong → **503 `partner_auth`** (not 401: kjbox's outbox drops records on any 4xx but 429, so a misconfigured or rotating secret must stay retryable); unset on the server → 503 `partner_disabled`. Both are logged at WARNING. The local stack (`DECIDE_TEST_API=1`, never production) defaults it to `local-stack-secret`.

| Route | Purpose |
|-------|---------|
| `PUT /api/partner/kjbox/karaoke-log/{kj_id}` | Body: the outbox record `{history_id, gen_email, source, box_id, night_id, night_date, entry_id, artist, title, sung_at, ended_at, state?, singers[], venue: {kind, name}, media}`. Resolves the decide profile by `gen_email` (created if missing). Idempotent; kjbox owns the facts, never the survey. → `{id, deleted, song_key_status}` (no user data). Missing `gen_email` → 400 `invalid`; unknown fields are ignored. |
| `DELETE /api/partner/kjbox/karaoke-log/{kj_id}?gen_email=` | The host un-did Done (`gen_email` may also be a JSON body field). 204, idempotent, also for unknown ids and for an email with no decide profile (never creates one). A later `PUT` (Done again) restores it; a user's own delete is never undone. |

`singers` must be a list of at most 6 strings of at most 100 characters each (else 400 `invalid`).

#### Errors (karaoke-log and partner routes)

decide's usual shape: `{"detail": "<code>"}` (FastAPI's own 422 body for schema errors).

| Status | Code | Routes | Retry? |
|--------|------|--------|--------|
| 400 | `invalid` | all (bad id prefix, field values, `since`, `cursor`, `singers`, missing `gen_email`) | No: fix the request |
| 401 | `Not authenticated` / `gen_session_*` | user routes (no or bad gen token) | After signing in again |
| 403 | `not_owner` | `PUT /api/my/karaoke-log/{kj_…}` with a kjbox-owned field | No |
| 404 | `not_found` | user `DELETE` of an unknown id | No |
| 422 | FastAPI validation | body not a JSON object; `limit` outside 1–500 | No |
| 503 | `partner_auth` | partner routes: missing or wrong `X-Kjbox-Secret` | **Yes** (fix the secret; the outbox keeps retrying) |
| 503 | `partner_disabled` | partner routes: secret unset on decide | **Yes** |
| 5xx | (other) | any (Firestore errors) | **Yes** |

### Partner: gen account deletion (`DELETE /api/partner/gen/users`)

Server to server from gen's account deletion. Body `{"email": str}`, header `X-Partner-Secret` = `DECIDE_GEN_PARTNER_SECRET` (`hmac.compare_digest`): missing or wrong → 401 `partner_auth`; unset on the server → 503 `partner_disabled`. Runs `delete_all_user_data` (every per-user store, voice recordings included) and returns `200 {"deleted": {collection: count}}`; an unknown or already-deleted email returns `{"deleted": {}}` (idempotent, so gen can retry).

### GET /api/quiz/progress

Returns the progress saved by `PUT /api/quiz/progress` (same shape), or 204 when there is none.

### Library filter on search

`GET /api/search` and `GET /api/search/popular` accept `library=any|community|karafun`. `karafun` means KaraokeNerds brand `KV` (KaraFun / Karaoke Version catalogue); `community` means a version of kind `community`.

## Test API (local stack only)

`/api/test/*` is mounted only when `DECIDE_TEST_API=1` **and** the environment is not production (`backend.main._mount_test_support`); in production the routes do not exist (404). No auth, so never expose it. `scripts/local-stack.sh` enables it. Users are gen users; the profile is `decide_users/{sha256(lowercase email)}`.

| Route | Purpose |
|-------|---------|
| `POST /api/test/profile/reset` | `{email}` → 204; deletes the user's decide data |
| `POST /api/test/profile/seed` | `{email, artists:[{name,affinity}], genres, decades, energy, quiz_completed, loved:[song_key], hidden:[song_key]}` → creates/overwrites the quiz profile and song feedback |
| `GET /api/test/profile?email=` | Reads the profile back |
| `POST /api/test/voice/seed-history` | `{email, sessions: [{state, result_summary?, attempts: [{song_key, artist, title, quiz, pitch?, note_labels?, recording, excluded?}]}]}` → `{user_id, sessions: [{session_id, attempt_ids, recording_ids}]}`. Replaces the user's voice history (oldest session first); `recording: true` stores a short synthetic WAV that `GET /api/voice/recordings/{id}/audio` plays (docs/TESTING.md § Voice session) |
| `POST /api/test/karaoke-log/seed` | `{email, entries: [{id: "app_…", ...app patch}]}` → `{user_id, count}`. Writes app karaoke-log entries through `KaraokeLogService.upsert_user` (counts and survey placeholders as a real app write); creates the user if needed. Non-`app_` ids → 400 `invalid`. |
| `POST /api/test/get-made/seed` | `{email, songs: [{artist, title, song_key?, loved?, times_sung?}]}` → `{user_id, keys}`. Writes `user_songs` rows (loved and / or sung, `source` `manual` when loved else `karaoke_log`) keyed like "Love it" rows (`loved_doc_id`), so a re-seed overwrites the same row. Use the fixture catalog's songs with no karaoke version ("Fixture Artist NN / Fixture Song NN-MM", `in_kn = 0`) so `GET /api/my/get-made` lists them. `song_key` defaults to `song_key(artist, title)`. Reset clears the user's `user_make_dismissed` docs. |
| `POST /api/test/crowd/seed` | `{song_key, total: {too_low, right, too_high}, by_bucket: {<voice type>: {too_low, right, too_high}}, crowd_pleaser_yes, crowd_pleaser_answered?}` → `{song_key, doc_id}`. Replaces that song's `song_crowd_stats` doc in the live shape (thresholds still apply on read). **Bucket votes are not added into `total`**: a real vote counts in both, so include them in `total` yourself; per answer, the buckets' sum must fit inside `total` (else 422). `crowd_pleaser_answered` defaults to `crowd_pleaser_yes` and must be ≥ it (422). Unknown buckets or answer names are 422. Not per user: profile reset never clears it. |
| `POST /api/test/crowd/reset` | `{song_key}` → 204. Deletes that song's `song_crowd_stats` doc (a missing doc is a no-op). Flows that seed a crowd reset it themselves. |
| `GET /api/test/karaoke-log?email=` | `{user_id, entries, sung, unresolved}`: every karaoke-log record (deleted ones and placeholders too, newest first), `user_songs` rows with a sung count, and the unresolved counts. Reset also clears the karaoke log and the user's playlists. |
| `POST /api/test/listenbrainz/poll` | Runs the daily `lb-poll` job's ListenBrainz poller once in-process (budget `lb_poll_budget_seconds`) → `{users_polled, listens_added, imports_checked, rate_limited, remaining}` |
| `PUT /api/test/fake-externals` | `{knobs: {metabrainz?: "deny", metabrainz_user?, spotify_embed?: "changed", screenshot?: "fail"}}` → `{knobs}`; resets every `DECIDE_FAKE_EXTERNALS` fake to its defaults, then sets the knobs (422 unknown knob; 409 `fake_externals_disabled` when the fakes are off, as for the two below) |
| `POST /api/test/fake-listenbrainz/users` | `{username, listens: [{artist, title, listened_at}]}` → creates/replaces a fake ListenBrainz user |
| `POST /api/test/fake-lastfm/users` | `{username, top_tracks: [{artist, title, playcount}], top_artists: [{name, playcount}]}` → creates/replaces a fake Last.fm user |

With `DECIDE_FAKE_EXTERNALS=1` (local stack only; refused in production) `/fake/*` serves fake ListenBrainz, MetaBrainz OAuth, Spotify embed and Last.fm endpoints: see [TESTING.md § Local stack: fakes](TESTING.md#local-stack-fakes).

## Song search ✅ Implemented

Served by the separate `karaoke-decide-search` Cloud Run service (`backend/search/app.py`). The Worker routes `/api/search` and `/api/search/*` there; every other `/api/*` path goes to the main API. No auth. Clients send `X-Device-Id`, which is used for rate limits; the per-IP limit uses the `X-Nomad-Client-IP` header the Worker sets from `CF-Connecting-IP` (client-supplied values are overwritten). OpenAPI: `/api/search/openapi.json`; Swagger UI: `/api/search/docs` (both reachable through the Worker).

**Rate limits:**
- Search endpoints: 300 requests per device and 1,500 per IP, per 5 minutes.
- `/resolve`: 20 per device and 200 per IP, per 5 minutes.
- Over the limit: `429` with `Retry-After`.

**Index not loaded:** endpoints that need the index return `503`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/search/health` | Always `{"ok": true}`. Never touches the index. Used for the uptime ping and the app's warm-up. |
| `GET /api/search/ready` | `{"ok": true}` once both indexes are loaded, else `503`. Used as the Cloud Run startup probe. |
| `GET /api/search?q=&limit=20&sid=` | Identify the song and list karaoke versions. Returns `{identification: {status, kind, best, candidates}, groups: [SongGroup], index: {run, built_at}, sid}`. A query that cleans up to nothing gives `status: "none"` and `groups: []`. |
| `GET /api/search/popular?limit=30` | `[SongGroup]`: the most popular songs that have karaoke versions. |
| `GET /api/search/song?key=<song_key>` | `{song: SongRef, versions: [Version]}`. `404` for an unknown key. |
| `GET /api/search/resolve?q=&sid=` | Gemini fallback through gen's `POST /api/kjbox/catalog/resolve`, authenticated with `DECIDE_GEN_PARTNER_SECRET`. Returns `{corrected, kind: "cosmetic"\|"content", typed, groups, split, alternatives}`, or `{}` when there is nothing to offer. |
| `POST /api/search/event` | `{sid, type, data}` → `204`. Client telemetry, written to the logs. |
| `GET /api/search/offline-index` | Manifest for the app's offline database. Returns `{run, schema, normalizer, url, bytes, sha256, ...}`. `url` is `https://decide.nomadkaraoke.com/offline-index/<run-token>/offline.db.gz`, from `SONG_INDEX_OFFLINE_URL_TEMPLATE`. `404` if the run has no offline file. |

The models are in `backend/search/models.py`:
- `SongRef`: `{artist, title, song_key, popularity, karaoke}`
- `Version`: `{brand_code, brand_name, kind, youtube_url, rank}`
- `SongGroup`: `{song_key, artist, title, version_count, versions}`

**Offline file download:** `GET https://decide.nomadkaraoke.com/offline-index/<run-token>/offline.db.gz`
- The Worker passes this through from the public bucket and caches it at the edge for 7 days. `GET` and `HEAD` only.
- Any other path shape is a `404`.
- Check the downloaded file against the manifest's `bytes` and `sha256`.

---

## Auth ✅ Implemented

### POST /api/auth/guest

> The web app no longer creates guests (sign-in with a gen email code is required); the endpoint remains for legacy clients. Guests move to the signed-in user via `POST /api/auth/claim-guest`.

Create a guest/anonymous user session. Allows users to try the app (quiz, recommendations) without email verification.

**No authentication required**

**Response:**
```json
{
  "access_token": "<jwt-token>",
  "token_type": "bearer",
  "expires_in": 2592000
}
```

Guest sessions last 30 days. Guest users cannot connect music services until they verify their email.

### POST /api/auth/magic-link

Request a magic link to be sent via email. In dev mode (no Postmark configured), the link is logged to console.

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "If an account exists for this email, you will receive a magic link shortly."
}
```

### POST /api/auth/verify

Verify a magic link token and get an access token.

**Request:**
```json
{
  "token": "<magic-link-token>"
}
```

**Response:**
```json
{
  "access_token": "<jwt-token>",
  "token_type": "bearer",
  "expires_in": 604800
}
```

### POST /api/auth/upgrade

Request to upgrade a guest account to a verified account. Sends a magic link to the provided email.

**Requires:** Bearer token (guest user)

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response:**
```json
{
  "message": "Verification email sent. Check your inbox to complete the upgrade."
}
```

When the user clicks the magic link, their guest data (quiz results, etc.) is migrated to the verified account. If the email already has an account, the guest data will be merged.

**Error Responses:**
- `400` - Account is already verified

### GET /api/auth/me

Get the current authenticated user.

**Response:**
```json
{
  "id": "user123",
  "email": "user@example.com",
  "display_name": "John Doe",
  "is_guest": false
}
```

### PUT /api/auth/profile

Update the current user's profile settings.

**Requires:** Bearer token

**Request:**
```json
{
  "display_name": "John Doe"
}
```

Use `null` to clear the display name:
```json
{
  "display_name": null
}
```

**Response:**
```json
{
  "id": "user123",
  "email": "user@example.com",
  "display_name": "John Doe"
}
```

**Error Responses:**
- `404` - User not found

### POST /api/auth/claim-guest

Move a guest profile's data (songs, artists, connected services, playlists, quiz answers) onto the signed-in gen user's profile, then delete the guest. Lists are unioned; the target's values win on conflict.

**Requires:** gen session Bearer token (legacy JWTs get `403 requires_gen_session`)

**Request:**
```json
{
  "guest_token": "<legacy guest jwt>"
}
```

**Response:**
```json
{
  "songs_moved": 12,
  "artists_moved": 8,
  "services_moved": 0,
  "playlists_moved": 1,
  "quiz_copied": true
}
```

**Error Responses:**
- `400 not_a_guest_token` - `guest_token` is not a valid guest JWT
- `403 requires_gen_session` - caller did not authenticate with a gen session token
- `404 guest_not_found` - guest profile does not exist (already claimed or deleted)
- `404 target_not_found` - the signed-in user's profile does not exist

### POST /api/auth/logout

Log out the current user (stateless - client should discard token).

**Requires:** Bearer token

**Response:**
```json
{
  "message": "Successfully logged out"
}
```

---

## Catalog

### GET /api/catalog/songs

Search and browse the karaoke catalog.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| q | string | Search query (artist or title) |
| artist | string | Filter by artist name |
| page | int | Page number (default: 1) |
| per_page | int | Results per page (default: 50, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "queen-bohemian-rhapsody",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "sources": ["karaokenerds"],
      "is_popular": true
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 50,
  "has_more": false
}
```

### GET /api/catalog/songs/{song_id}

Get details for a specific song.

**Response:**
```json
{
  "id": "queen-bohemian-rhapsody",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "sources": ["karaokenerds"],
  "is_popular": true
}
```

### GET / PUT / DELETE /api/my/voice

The singer's voice profile, stored on their `decide_users` doc (auth required; verified, guest and gen-session users). Mic audio never reaches the server: the app sends only derived numbers, and unknown body keys are rejected (422).

- `GET` -> `{"profile": null | {...}, "mic": null | {...}, "latest_session": null | {...}}` (an invalid stored profile is treated as unset). `profile` is the saved profile (what recommendations use). `mic` is the last mic-test result, kept as `voice_profile_mic` beside a session result (so it survives a session saving over it). `latest_session` is the newest session result (by when the session started, `created_at`) resting on at least one counting attempt with range evidence, saved or not, in the `GET /api/voice/history` `result` shape; Me shows it next to `mic`.
- `PUT` replaces the profile (last save wins) and returns it with `updated_at`; a `method: mic` profile is also kept as the mic result. 404 `not_found` if the user has no doc; 422 on an inconsistent body.
- `DELETE` -> 204; clears the profile and the kept mic result.

```json
{"low_midi": 40, "high_midi": 69, "comfort_low": 45, "comfort_high": 64, "voice_type": "baritone", "voice_type_between": "tenor",
 "register": null, "confidence": "medium", "method": "mic", "measured_at": "2026-10-22T10:00:00Z"}
```

Rules: MIDI 24-96; `low <= comfort_low <= comfort_high <= high`; exactly one of `voice_type` (bass|baritone|tenor|alto|mezzo|soprano) / `register` (lower|middle|higher); `method` mic|self (`session` is `422` here: only a voice session's result saves those), and `self` requires `confidence: low`.

Optional since v0.33.0 (null when absent, so old apps and stored profiles are unchanged): `falsetto_high` / `falsetto_low` (MIDI 24-96, `falsetto_high >= low_midi`, `falsetto_low <= falsetto_high`): the falsetto / head-voice range, kept apart from the chest range `low_midi..high_midi` (the mic test's "Was your top note falsetto?"); `experience` (none|some|trained) and `uses_falsetto` (yes|sometimes|no|unsure): the singing background from the session setup. A session profile carries the result's `falsetto_high` and its setup's background.

### GET /api/catalog/songs/{song_key}/vocal-range

Public. A song's vocal range from `catalog.db` (MIDI numbers; `tier: rough` = the artist's range standing in; `is_duet` null when unknown). `range` is `null` for an unknown song or one without data. `503 warming_up` (`Retry-After: 30`) until `catalog.db` has loaded. URL-encode the key (`muse%7Cuprising`).

```json
{"song_key": "muse|uprising", "range": {"low": 48.0, "high": 70.0, "p10": 52.0, "p50": 59.0, "p90": 66.0, "tier": "notes", "octave_confidence": 0.92, "is_duet": false}}
```

### GET /api/catalog/songs/{song_id}/links

Get karaoke links for a specific song. Returns URLs to find or create karaoke videos.

**Response:**
```json
{
  "song_id": 1,
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "links": [
    {
      "type": "youtube_search",
      "url": "https://www.youtube.com/results?search_query=Queen+Bohemian+Rhapsody+karaoke",
      "label": "Search YouTube",
      "description": "Find existing karaoke videos on YouTube"
    },
    {
      "type": "karaoke_generator",
      "url": "https://gen.nomadkaraoke.com?artist=Queen&title=Bohemian+Rhapsody",
      "label": "Create with Generator",
      "description": "Generate a custom karaoke video with Nomad Karaoke"
    }
  ]
}
```

**Link Types:**
- `youtube_search` - YouTube search results for karaoke videos
- `karaoke_generator` - Link to Nomad Karaoke Generator to create custom videos

### GET /api/catalog/artists

Search artists by name for autocomplete. Returns artists sorted by popularity.

**MBID-first:** Uses MusicBrainz as primary identifier with Spotify enrichment.

**Fallback:** with `BIGQUERY_DISABLED` (local stack) or when BigQuery errors, results come from catalog.db's `artists` table (exact, then prefix, then word-prefix, then substring match; popularity order; no `spotify_id`/`disambiguation`). Empty while catalog.db is warming up; never a 500.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| q | string | Search query (min 2 characters) |
| limit | int | Max results (default: 10, max: 50) |

**Response:**
```json
{
  "artists": [
    {
      "mbid": "0383dadf-2a4e-4d10-a46a-e9e041da8eb3",
      "name": "Queen",
      "disambiguation": "UK rock group",
      "artist_type": "Group",
      "tags": ["rock", "classic rock", "glam rock"],
      "spotify_id": "1dfeR4HaWDbWqFHLkxsg1d",
      "popularity": 88,
      "genres": ["rock", "classic rock", "glam rock"],
      "artist_id": "1dfeR4HaWDbWqFHLkxsg1d",
      "artist_name": "Queen"
    }
  ],
  "total": 1
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| mbid | string\|null | MusicBrainz artist UUID (primary identifier) |
| name | string | Artist name |
| disambiguation | string\|null | Descriptive text to distinguish same-named artists |
| artist_type | string\|null | Person, Group, Orchestra, etc. |
| tags | array | MusicBrainz community tags |
| spotify_id | string\|null | Spotify artist ID (for images/links) |
| popularity | int | Spotify popularity (0-100) |
| genres | array | Spotify algorithmic genres |
| artist_id | string | **Deprecated:** Use `mbid` or `spotify_id` |
| artist_name | string | **Deprecated:** Use `name` |

### GET /api/catalog/artists/index

Get the full artist index for client-side autocomplete. Returns ~24K popular artists (popularity >= 50) in a compact format optimized for download to the browser.

**MBID-first:** Uses MusicBrainz as primary identifier with Spotify enrichment.

This endpoint is designed to be cached aggressively. Response is ~600KB with brotli compression. The frontend uses Fuse.js for instant fuzzy search against this index.

**Response:**
```json
{
  "artists": [
    {
      "m": "ab67616d-0000-0000-0000-000000000000",
      "i": "4q3ewBCX7sLwd24euuV69X",
      "n": "Bad Bunny",
      "p": 100
    }
  ],
  "count": 24446
}
```

**Compact format keys:**
- `m` - mbid (MusicBrainz ID, primary - may be null for Spotify-only)
- `i` - spotify_id (for images/backward compat)
- `n` - name
- `p` - popularity (0-100)

### GET /api/catalog/tracks

Search tracks for autocomplete. Searches Spotify first, then supplements with MusicBrainz recordings when results are sparse (e.g., deep cuts not in Spotify's catalog).

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| q | string | Yes | Search query (min 2 characters) |
| artist | string | No | Filter by artist name (unicode-aware, e.g., "Maximo Park" matches "Maxïmo Park") |
| limit | integer | No | Max results (1-50, default 10) |

**Response:**
```json
{
  "tracks": [
    {
      "track_id": "6rqhFgbbKwnb9MLmUQDhG6",
      "track_name": "Apply Some Pressure",
      "artist_name": "Maxïmo Park",
      "artist_id": "0kfQafj4QiWiXaM0EGWbhV",
      "popularity": 55,
      "duration_ms": 228000,
      "explicit": false,
      "recording_mbid": null
    },
    {
      "track_id": "mb:a74b1b7f-71a5-4011-9441-d0b5e4122711",
      "track_name": "Limassol",
      "artist_name": "Maxïmo Park",
      "artist_id": "",
      "popularity": 0,
      "duration_ms": 215000,
      "explicit": false,
      "recording_mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    }
  ],
  "total": 2
}
```

**Notes:**
- `track_id` is a Spotify track ID when available, or `"mb:{recording_mbid}"` for MusicBrainz-only tracks
- `recording_mbid` is set for tracks sourced from MusicBrainz
- When `artist` is provided, the popularity threshold is lowered to surface less popular tracks

### GET /api/catalog/stats

Get catalog statistics.

**Response:**
```json
{
  "total_songs": 50000,
  "total_artists": 12000,
  "last_updated": "2024-01-15T03:00:00Z"
}
```

---

## Services ✅ Implemented

**Note:** All services endpoints except `POST /api/services/youtube-music/import` require a verified (non-guest) user. Guest users receive a `403 Forbidden` response with message: "Email verification required. Please verify your email to use this feature."

### GET /api/services

List connected music services.

**Requires:** Bearer token (verified user only)

**Response:**
```json
[
  {
    "service_type": "spotify",
    "service_username": "SpotifyUser123",
    "last_sync_at": "2024-12-30T12:00:00Z",
    "sync_status": "idle",
    "sync_error": null,
    "tracks_synced": 150
  }
]
```

### POST /api/services/spotify/connect

Start Spotify OAuth flow. Returns an authorization URL to redirect the user.

**Requires:** Bearer token

**Response:**
```json
{
  "auth_url": "https://accounts.spotify.com/authorize?..."
}
```

### GET /api/services/spotify/callback

Spotify OAuth callback. Called by Spotify after user authorization.
Redirects to frontend success/error page.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| code | string | Authorization code from Spotify |
| state | string | CSRF protection state token |
| error | string | Error code if auth failed |

**Response:** Redirect to `{frontend_url}/services/spotify/success` or `{frontend_url}/services/spotify/error?message=<code>`. The code is a fixed value. It is never Spotify's raw error text or an exception message.

| `message` | Meaning |
|-----------|---------|
| `denied` | The user declined on Spotify (`error=access_denied`) |
| `spotify_error` | Spotify returned any other `error` |
| `missing_params` | No `code` or `state` |
| `invalid_state` | The state is malformed, unknown or expired |
| `exchange_failed` | The token exchange or profile fetch failed (details are logged at WARNING) |

A reconnect clears a `needs_reconnect` flag (`error_code`) and an `error` sync status. Those are left when a refresh token is revoked (Spotify `invalid_grant`).

### POST /api/services/lastfm/connect

Connect Last.fm account by username.

**Requires:** Bearer token

**Request:**
```json
{
  "username": "lastfmuser"
}
```

**Response:**
```json
{
  "service_type": "lastfm",
  "service_username": "lastfmuser",
  "last_sync_at": null,
  "sync_status": "idle",
  "sync_error": null,
  "tracks_synced": 0
}
```

### GET /api/services/listenbrainz/oauth/start

Verified users. Start connecting ListenBrainz through MetaBrainz OAuth (authorization code + PKCE S256, scopes `profile listenbrainz:submit-listens`). Query `return_to=app|web` (required). Response `{"auth_url": "https://metabrainz.org/oauth2/authorize?..."}`; `404 {"detail":"oauth_disabled"}` when `capabilities.listenbrainz_oauth` is false.

### POST /api/services/listenbrainz/upload-export

Verified users, ListenBrainz connected with OAuth. Multipart `file` (a Spotify export `.zip`, at most 31,457,280 bytes). Hands the zip to ListenBrainz's importer (`POST /1/import-listens/`, `service=spotify`) with the user's token; nothing is ingested here. Response `{"import_id":"42","status":"waiting"}`, and `lb_pending_import={id,status,submitted_at}` is stored on the connection (shown as `pending_listenbrainz_import` in `/summary`). Errors (`detail`): `409 not_oauth_connected | needs_reconnect | import_in_progress`, `413 too_large`, `400 not_a_zip` (no `PK\x03\x04` magic), `429 rate_limited` (with `Retry-After`), `503 listenbrainz_unavailable` (ListenBrainz down, or the token refresh is busy or MetaBrainz failed transiently: `Retry-After: 30`; retry).

### GET /api/services/listenbrainz/oauth/callback

No auth (MetaBrainz redirects here; the one-time state identifies the user; a state outside `^[A-Za-z0-9_-]{16,128}$` is `invalid_state` without a lookup). Query `code`, `state`, `error`. Stores the connection (`auth: "oauth"`, tokens encrypted with `DECIDE_TOKEN_KEY`) and answers `302` with `Cache-Control: no-store`:
- `return_to=app`: `nomadkaraoke://services/listenbrainz?status=connected` or `?status=error&reason=<code>` (tiny HTML body with a "Return to the app" link);
- `return_to=web` (also used when the state is unknown): `{frontend_url}/en/music-i-know?listenbrainz=connected` or `?listenbrainz=error&reason=<code>`.

Reason codes: `denied`, `missing_params`, `invalid_state`, `exchange_failed`, `validate_failed` (no ListenBrainz account for that MetaBrainz user). The Worker proxies with `redirect: "manual"` so the 302 reaches the browser.

### GET /api/services/capabilities

No auth. Feature flags for the mobile Connect hub: `{"screenshot_import": bool, "listenbrainz_oauth": bool, "listenbrainz_upload_max_bytes": 31457280}`.
`screenshot_import` is true when `GEMINI_API_KEY` is set (or fakes are on); `listenbrainz_oauth` needs the MetaBrainz client id/secret, `DECIDE_TOKEN_KEY` and `LISTENBRAINZ_OAUTH_ENABLED`.

### GET /api/services/summary

Verified users. Every source the user has, for the app's "Connected sources" list: each `music_services` connection merged with its `user_imports` doc, plus import-only sources. `artists` / `songs` are Firestore aggregation counts of the rows whose primary `source` is that source (a row's `source` is the first source that wrote it; sources that only merged into someone else's row, listed in `sources`, are not counted).

**Response:** `{"sources":[{"source":"lastfm","connected":true,"username":"x","artists":0,"songs":0,"last_import_at":null,"status":"ok","auth":"username"|"oauth"|null,"pending_listenbrainz_import":{"id":"...","status":"..."}|null}],"capabilities":{...same as /capabilities},"known_songs":42}`. `status` (first match wins): `importing` (syncing, or import importing) > `needs_reconnect` (`error_code`) > `stale` (`lb_stale`) > `error` > `ok`.

`known_songs` (top level, an int) is the number of distinct songs the recommender treats as known, across all sources: `user_songs` rows deduped by `song_key` (else `"u:" + song_key(artist, title)`), counted when loved or when the best of its sources has a positive weight. Voice-session-only unloved rows don't count. It is 0 for a user with no rows.

`GET /api/services` keeps its shape and gains `artists`, `songs`, `last_import_at`, `status`. If the per-source lookup fails it still answers `200` with those fields as `0`, `0`, `null`, `"ok"`.

### DELETE /api/services/sources/{source}/data

Verified users. Remove what a source contributed and its connection, if any. Rows that were sung or loved, or are held by another source, are kept. `source` is one of `screenshot, spotify_playlist, spotify_export, apple_export, youtube_music, listenbrainz, lastfm, spotify, apple_music` (else `400`). Export sources also drop their running artist sums.

**Response:** `{"removed_songs":0,"removed_artists":0}`

### POST /api/services/screenshot/extract

Read artists and songs off 1-5 music-app screenshots with Gemini Flash. Multipart field `images` (each at most 4 MiB, `image/*`). Limit: 10 images per user per UTC day (reserved atomically, released if Gemini fails). Nothing is saved yet.

**Requires:** Bearer token. **Response:** `{"candidates":[{"kind":"artist"|"song","artist":"Muse","title":"Hysteria"|null,"confidence":0.9,"matched":true,"song_key":"muse|hysteria"|null}]}` (deduped, matched first).

**Errors:** `400` `no_images` / `too_many_images` / `not_an_image` · `413` `image_too_large` · `429` `daily_limit` · `503` `screenshot_unavailable` (disabled, or Gemini error).

### POST /api/services/screenshot/confirm

Save the candidates the user kept (`source: "screenshot"`). Body `{"items":[{"kind":"artist"|"song","artist":"...","title":"..."}]}` (at most 200).

**Response:** `{"source":"screenshot","artists_added":0,"songs_added":0,"songs_updated":0,"tracks_matched":0}`

### POST /api/services/spotify-export and /api/services/apple-export

One chunk of a privacy-export import; the phone parses the zip and posts aggregates (at most 20,000 tracks per chunk, `chunk_count` 1..50). Body: `{generated_at, chunk_index, chunk_count, tracks:[{artist,title,ms_played,plays,first_ts,last_ts}]}`; Spotify adds `kind: "account"|"extended"`, `liked:[{artist,title}]` (at most 20,000) and `playlists:[{name,tracks:[{artist,title}]}]` (at most 500). Ingested with max play counts, so re-importing an export, or an Account then an Extended export, never doubles plays. A chunk already recorded for `(kind, generated_at)` is a no-op returning the stored totals with `duplicate: true`. Import status is `importing` until every chunk is in, then `ok`.

**Response:** `{"source":"spotify_export","artists_added":0,"songs_added":0,"songs_updated":0,"tracks_matched":0,"duplicate":false,"chunks_done":1,"chunk_count":1}` (totals are cumulative over the export's chunks).

**Errors:** `400` `bad_chunk` (index >= count, or count differs from the export's first chunk) · `422` validation.

### POST /api/services/youtube-music/import

Import songs from a public or unlisted YouTube Music / YouTube playlist (no YouTube login).
Used on the quiz "Artists You Know" step so YouTube Music users can share e.g. a copy of their
Liked Music. Tracks (up to 1000; 500 for guests) are matched to the catalog and stored as `user_songs` with
`source: "youtube_music"`; unmatched tracks are kept for "Create Your Own Karaoke".

**Requires:** Bearer token (guests allowed — songs migrate on email verification)

**Request:**
```json
{
  "playlist_url": "https://music.youtube.com/playlist?list=PL..."
}
```

**Response:**
```json
{
  "playlist_title": "My Likes",
  "tracks_fetched": 214,
  "tracks_matched": 87
}
```

**Errors:** `400` not a YouTube playlist link · `422` the always-private Liked Music (`LM`) /
Liked videos (`LL`) playlist was shared · `404` playlist missing or private · `429` rate limited
(one successful import per 30s, 20/day per user — checked and reserved atomically in a Firestore transaction) · `503` YouTube unreachable (after one automatic retry).

### POST /api/services/spotify-playlist/import

Import up to 100 tracks from a public Spotify playlist link (no Spotify login). Best-effort: it reads
the undocumented `open.spotify.com/embed/playlist/{id}` page (`__NEXT_DATA__`), so a Spotify layout
change degrades it. A daily GitHub workflow (`spotify-embed-canary.yml`) posts to Discord #noise when
parsing breaks. Tracks are ingested with `source: "spotify_playlist"`.

**Requires:** Bearer token (verified user)

**Request:** `{"url": "https://open.spotify.com/playlist/..."}` (also `spotify:playlist:` URIs, bare
22-char ids, or share text containing a link)

**Response:** `{"playlist_title": "...", "tracks_fetched": 50, "tracks_matched": 20}`

**Errors:** `400` not a Spotify playlist link · `404` missing or private · `422` Spotify changed its
embed page · `429` rate limited (same 30s cooldown / 20 per day as YouTube Music, separate counter) ·
`503` Spotify unreachable.

### Apple Music (MusicKit)

Users sign in to Apple Music on decide web with MusicKit JS (`/connect/apple-music`). decide stores
the Music User Token (about 6 months) as the `apple_music` service's access token, and sync reads
recent plays (up to 150) and library songs (up to 3,000) from `/v1/me/*`. A refused token marks the
service `error` with "reconnect". Keys: Secret Manager `apple-musickit-private-key` (.p8),
`apple-musickit-key-id`, `apple-team-id` (Media ID `media.com.nomadkaraoke.mobile`).

- `GET /api/services/apple-music/developer-token` (public) → `{developer_token, expires_at}`. An
  ES256 JWT with an `origin` claim of `FRONTEND_URL`, so it only works from decide web. 503 when the
  key isn't configured.
- `GET /api/services/apple-music/app-developer-token` (signed in) → `{developer_token, expires_at}`
  without an `origin` claim, for the iOS app's native MusicKit sign-in (`DefaultMusicTokenProvider`
  turns it into a Music User Token, which the app posts to `/connect` signed in, no browser).
- `POST /api/services/apple-music/link` (signed in) → `{connect_url, expires_in}`. For the app: a
  one-time `state` (10 min) in `connect_url` (`{FRONTEND_URL}/{locale}/connect/apple-music?state=…&return=app`),
  so the page works in an in-app browser that isn't signed in to decide. The page returns to
  `nomadkaraoke://music-connected?service=apple_music`.
- `GET /api/services/apple-music/link-info?state=` (public) → `{account}`: the link owner's masked
  email (`a•••@example.com`), shown on the page so a forwarded link can't take your music. 404 when
  expired. Doesn't consume the state.
- `POST /api/services/apple-music/connect` `{music_user_token, state?}` → `{service, sync_job_id}`.
  Signed in, or with `state` from `/link` (consumed only after Apple accepts the token, so a refused
  attempt can be retried). Checks the token against Apple
  (`/v1/me/storefront`), stores it and starts a sync job. 401 no user / bad or expired state; 422
  Apple refused the token; 503 not configured. `sync_job_id` is null if the job couldn't be queued.

### DELETE /api/services/{service_type}

Disconnect a music service.

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| service_type | string | "spotify" or "lastfm" |

**Response:**
```json
{
  "message": "Successfully disconnected spotify"
}
```

### POST /api/services/sync

Trigger async listening history sync for all connected services. Sync runs in background via Cloud Tasks.

**Requires:** Bearer token

**Response (202 Accepted):**
```json
{
  "job_id": "sync_user123_1735621234",
  "status": "pending",
  "message": "Sync job created. Poll /api/services/sync/status for progress."
}
```

If the user's latest job is stalled, it is re-enqueued and returned with `"status": "stalled"` instead of a new job.

### GET /api/services/sync/status

Get current sync status for all connected services and any active sync job.

**Requires:** Bearer token

**Response:**
```json
{
  "services": [
    {
      "service_type": "spotify",
      "service_username": "SpotifyUser123",
      "last_sync_at": "2024-12-30T12:00:00Z",
      "sync_status": "idle",
      "sync_error": null,
      "tracks_synced": 150
    }
  ],
  "active_job": {
    "job_id": "sync_user123_1735621234",
    "status": "in_progress",
    "progress": {
      "current_service": "spotify",
      "current_phase": "matching",
      "total_tracks": 500,
      "processed_tracks": 250,
      "matched_tracks": 180,
      "percentage": 50
    },
    "results": null,
    "error": null,
    "created_at": "2024-12-30T12:00:00Z",
    "completed_at": null
  }
}
```

**Job statuses:** `pending`, `in_progress`, `stalled`, `completed`, `failed`

`stalled` is computed when the status is read; it is never stored. It means the job should be running but nothing has touched it. For an `in_progress` job that is no heartbeat for 300 s, because its instance died or hung. For a `pending` job it is 900 s, because its Cloud Task was lost or ran out of retries. A Cloud Tasks retry, or the next `POST /api/services/sync`, resumes a stalled job from its checkpoint.

---

## Quiz ✅ Implemented

### GET /api/quiz/artists

Get quiz artists for onboarding (recommended). Returns popular karaoke artists with their top songs for users to identify which they know. Each selected artist adds multiple songs to the user's library.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| count | int | Number of artists (default: 25, min: 10, max: 50) |
| genres | list[str] | Filter by genre IDs (e.g., `genres=rock&genres=pop`) |
| exclude | list[str] | Exclude artist names for pagination (e.g., `exclude=Queen&exclude=ABBA`) |

**Response:**
```json
{
  "artists": [
    {
      "name": "Queen",
      "song_count": 45,
      "top_songs": ["Bohemian Rhapsody", "Don't Stop Me Now", "We Will Rock You"],
      "total_brand_count": 312,
      "primary_decade": "1970s",
      "genres": ["classic rock", "glam rock", "arena rock"],
      "image_url": null
    }
  ]
}
```

**Note:** Genre filtering requires the Spotify artist genres table to be populated via ETL. Falls back to unfiltered results if table doesn't exist.

### POST /api/quiz/artists/smart

Get smart quiz artists informed by user's preferences and previous quiz step selections. This is an enhanced version of `/api/quiz/artists` that uses multiple signals to find more relevant artist suggestions:

- Genres: Filter to artists in selected genres
- Decades: Filter to artists active in selected decades
- Manual artists: Find artists in similar genres
- Manual song artists: Use genres from songs user enjoys singing

Each returned artist includes a `suggestion_reason` explaining why they were suggested.

**Fallback:** with `BIGQUERY_DISABLED` (local stack) or when the BigQuery path errors, suggestions come from catalog.db: `artist_similar` of the picks (`fans_also_like`, "Fans of X also like"), then the most popular catalog artists in the chosen genres/decades (`genre_match` / `decade_match`), padded with any popular artists (`popular_choice`). Empty while catalog.db is warming up; never a 500.

**Requires:** Bearer token

**Request:**
```json
{
  "genres": ["rock", "punk"],
  "decades": ["1990s", "2000s"],
  "manual_artists": ["Green Day", "Blink-182"],
  "manual_song_artists": ["Sum 41"],
  "exclude": ["Nirvana", "Pearl Jam"],
  "count": 15
}
```

| Field | Type | Description |
|-------|------|-------------|
| genres | list[str] | User's selected genre IDs |
| decades | list[str] | User's selected decades |
| manual_artists | list[str] | Artists manually entered by user |
| manual_song_artists | list[str] | Artists from songs user enjoys singing |
| exclude | list[str] | Artists to exclude (already shown/selected) |
| count | int | Number of artists to return (default: 25, max: 50) |

**Response:**
```json
{
  "artists": [
    {
      "name": "The Offspring",
      "song_count": 28,
      "top_songs": ["Self Esteem", "Pretty Fly (For a White Guy)", "The Kids Aren't Alright"],
      "total_brand_count": 156,
      "primary_decade": "1990s",
      "genres": ["punk rock", "skate punk", "alternative rock"],
      "image_url": null,
      "suggestion_reason": {
        "type": "similar_artist",
        "display_text": "Similar to Green Day",
        "related_to": "Green Day"
      }
    },
    {
      "name": "Foo Fighters",
      "song_count": 35,
      "top_songs": ["Everlong", "Learn to Fly", "Best of You"],
      "total_brand_count": 198,
      "primary_decade": "1990s",
      "genres": ["alternative rock", "post-grunge", "rock"],
      "image_url": null,
      "suggestion_reason": {
        "type": "genre_match",
        "display_text": "Based on rock",
        "related_to": null
      }
    }
  ],
  "has_more": true
}
```

**Suggestion Reason Types (priority order):**
| Type | Description | Example |
|------|-------------|---------|
| `fans_also_like` | Liked by users with similar taste (≥3 shared artists, ≥5 similar users) | "Liked by fans of Green Day, Blink-182 & Sum 41" |
| `similar_artist` | Shares 2+ genres with a user's manual artist | "Similar to Green Day" |
| `genre_match` | Matches user's selected genres | "Based on punk & rock" |
| `decade_match` | Artist's primary decade matches user's selection | "Popular in the 1990s" |
| `popular_choice` | Fallback for popular artists with no specific match | "Popular karaoke choice" |

### GET /api/quiz/decade-artists

Get example artists for each decade. Useful for helping users understand what era each decade represents.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artists_per_decade | int | Artists per decade (default: 5, min: 3, max: 10) |

**Response:**
```json
{
  "decades": [
    {
      "decade": "1980s",
      "artists": [
        { "name": "Michael Jackson", "top_song": "Billie Jean" },
        { "name": "Prince", "top_song": "Purple Rain" },
        { "name": "Madonna", "top_song": "Like a Prayer" }
      ]
    }
  ]
}
```

### GET /api/quiz/songs

Get quiz songs for onboarding (legacy). Returns popular karaoke songs for users to identify which they know.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| count | int | Number of songs (default: 20, min: 5, max: 30) |

**Response:**
```json
{
  "songs": [
    {
      "id": "1",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "decade": "1970s",
      "popularity": 85,
      "brand_count": 8
    }
  ]
}
```

### GET /api/quiz/progress

Returns the progress last saved by `PUT /api/quiz/progress`, in the same shape as that request (missing lists default to `[]`), or `204 No Content` when nothing was saved.

**Requires:** Bearer token

### PUT /api/quiz/progress

Save partial quiz progress for auto-save functionality. Allows frontend to persist quiz progress as the user fills in the form, enabling resume if they close the browser.

**Requires:** Bearer token

**Request:**
```json
{
  "step": 3,
  "genres": ["rock", "pop"],
  "decades": ["1980s", "1990s"],
  "artist_affinities": [
    {"artist_name": "Queen", "affinity": "love"},
    {"artist_name": "ABBA", "affinity": "like"}
  ],
  "manual_artists": [
    {"artist_id": "abc123", "artist_name": "Green Day", "genres": ["punk rock"]}
  ],
  "enjoy_songs": [],
  "energy_preference": "high",
  "vocal_comfort_pref": "easy",
  "crowd_pleaser_pref": "hits"
}
```

| Field | Type | Description |
|-------|------|-------------|
| step | int | Current quiz step (1-5) |
| genres | list[str] | Selected genre IDs |
| decades | list[str] | Selected decades |
| artist_affinities | list[obj] | Artists with affinity level |
| manual_artists | list[obj] | Manually added artists |
| enjoy_songs | list[obj] | Songs user enjoys singing |
| energy_preference | str | "chill", "medium", or "high" |
| vocal_comfort_pref | str | "easy", "challenging", or "any" |
| crowd_pleaser_pref | str | "hits", "deep_cuts", or "any" |

**Affinity Levels:**
| Level | Description | Recommendation Weight |
|-------|-------------|----------------------|
| occasionally | Listen to sometimes | 1x |
| like | Generally enjoy | 2x |
| love | Absolutely love | 3x |

**Response:**
```json
{
  "saved": true
}
```

**Notes:**
- Progress is stored per-user and overwrites previous progress
- Frontend debounces calls (3 second idle threshold)
- Progress is cleared when quiz is submitted

### POST /api/quiz/submit

Submit quiz responses with artist affinities and preferences.

**Requires:** Bearer token

**Request (with affinity - recommended):**
```json
{
  "artist_affinities": [
    {"artist_name": "Queen", "affinity": "love"},
    {"artist_name": "ABBA", "affinity": "like"},
    {"artist_name": "Elton John", "affinity": "occasionally"}
  ],
  "decade_preference": "1980s",
  "energy_preference": "high"
}
```

**Request (legacy - plain artist list):**
```json
{
  "known_artists": ["Queen", "ABBA", "Elton John"],
  "decade_preference": "1980s",
  "energy_preference": "high"
}
```

**Request (Song-based - legacy):**
```json
{
  "known_song_ids": ["1", "2", "3"],
  "decade_preference": "1980s",
  "energy_preference": "high"
}
```

**Response:**
```json
{
  "message": "Quiz completed successfully",
  "songs_added": 3,
  "recommendations_ready": true
}
```

**Notes:**
- `artist_affinities` is the recommended format with three affinity levels
- `known_artists` still supported for backwards compatibility (treated as "like" affinity)
- Affinity weights influence recommendation scoring

### GET /api/quiz/status

Get user's quiz completion status.

**Requires:** Bearer token

**Response:**
```json
{
  "completed": true,
  "completed_at": "2024-01-15T12:00:00Z",
  "songs_known_count": 5
}
```

### POST /api/quiz/enjoy-singing

Submit songs the user enjoys singing from quiz step 4. Bulk submission of enjoy-singing metadata.

**Requires:** Bearer token

**Request:**
```json
{
  "songs": [
    {
      "song_id": "1",
      "singing_tags": ["crowd_pleaser"],
      "singing_energy": "upbeat_party",
      "vocal_comfort": "comfortable",
      "notes": "My go-to opener!"
    },
    {
      "song_id": "spotify:4iV5W9uYEdYUVa79Axb7Rh",
      "singing_tags": ["shows_range", "nostalgic"]
    }
  ]
}
```

**Response:**
```json
{
  "submitted": 2,
  "created_new": 1,
  "updated_existing": 1,
  "failed": 0,
  "errors": []
}
```

**Notes:**
- All metadata fields are optional for each song
- Accepts both karaoke catalog IDs and Spotify track IDs (prefixed with "spotify:")
- Returns counts of created, updated, and failed entries

---

## My Songs ✅ Implemented

### GET /api/my/songs

Get songs from user's listening history matched to karaoke catalog.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1, min: 1) |
| per_page | int | Results per page (default: 20, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "user123:1",
      "song_id": "1",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "source": "spotify",
      "play_count": 10,
      "playcount": 250,
      "rank": 5,
      "spotify_popularity": 85,
      "is_saved": true,
      "times_sung": 2
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 20,
  "has_more": true
}
```

### GET /api/my/recommendations

Get personalized karaoke song recommendations.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Number of recommendations (default: 20, min: 1, max: 50) |
| decade | string | Filter by decade (e.g., "1980s") |
| min_popularity | int | Minimum popularity (0-100) |

**Response:**
```json
{
  "recommendations": [
    {
      "song_id": "100",
      "artist": "Queen",
      "title": "We Will Rock You",
      "score": 0.85,
      "reason": "You listen to Queen",
      "reason_type": "known_artist",
      "brand_count": 8,
      "popularity": 80
    }
  ]
}
```

**Reason Types:**
- `known_artist` - Song by an artist you already listen to
- `similar_genre` - Similar genre to your preferences
- `decade_match` - Matches your decade preference from quiz
- `crowd_pleaser` - Popular karaoke song for all audiences
- `popular` - High popularity score on streaming services
- `generate_karaoke` - Song without karaoke version (use Generator)

### GET /api/my/recommendations/categorized

Get recommendations organized into categories with rich filtering options.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| has_karaoke | bool | Filter by karaoke availability (true/false/null for all) |
| min_popularity | int | Minimum Spotify popularity (0-100) |
| max_popularity | int | Maximum Spotify popularity (0-100, for "hidden gems") |
| exclude_explicit | bool | Hide explicit content (default: false) |
| min_duration_ms | int | Minimum song duration in milliseconds |
| max_duration_ms | int | Maximum song duration in milliseconds |
| classics_only | bool | Only show songs with brand_count >= 20 (default: false) |

**Response:**
```json
{
  "from_artists_you_know": [
    {
      "song_id": "40718",
      "artist": "Elton John",
      "title": "I'm Still Standing",
      "score": 0.779,
      "reason": "You listen to Elton John",
      "reason_type": "known_artist",
      "brand_count": 21,
      "popularity": 84,
      "has_karaoke_version": true,
      "is_classic": true,
      "duration_ms": null,
      "explicit": false
    }
  ],
  "create_your_own": [
    {
      "song_id": "spotify:sub-focus:push-the-tempo",
      "artist": "Sub Focus",
      "title": "Push The Tempo",
      "score": 0.35,
      "reason": "Generate karaoke for Sub Focus",
      "reason_type": "generate_karaoke",
      "brand_count": 0,
      "popularity": 50,
      "has_karaoke_version": false,
      "is_classic": false,
      "duration_ms": 180000,
      "explicit": false
    }
  ],
  "crowd_pleasers": [
    {
      "song_id": "100",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "score": 0.65,
      "reason": "Popular karaoke song",
      "reason_type": "crowd_pleaser",
      "brand_count": 50,
      "popularity": 90,
      "has_karaoke_version": true,
      "is_classic": true,
      "duration_ms": null,
      "explicit": false
    }
  ],
  "total_count": 35,
  "filters_applied": {
    "has_karaoke": null,
    "min_popularity": null,
    "max_popularity": null,
    "exclude_explicit": false,
    "min_duration_ms": null,
    "max_duration_ms": null,
    "classics_only": false
  }
}
```

**Categories:**
- `from_artists_you_know` - Karaoke songs by artists in your library (max 15, 3 per artist)
- `create_your_own` - Songs from your library without karaoke versions (max 10)
- `crowd_pleasers` - Popular karaoke songs for discovery (max 10)

---

## My Data ✅ Implemented

User data management endpoints for the unified My Data page.

### GET /api/my/data/summary

Get aggregated summary of user's data.

**Requires:** Bearer token

**Response:**
```json
{
  "services": {
    "spotify": {
      "connected": true,
      "username": "SpotifyUser123",
      "tracks_synced": 150,
      "last_sync_at": "2024-12-30T12:00:00Z"
    },
    "lastfm": {
      "connected": false
    }
  },
  "artists": {
    "total": 25,
    "by_source": {
      "spotify": 20,
      "quiz": 5
    }
  },
  "songs": {
    "total": 150,
    "with_karaoke": 120
  },
  "preferences": {
    "completed": true,
    "decade": "1980s",
    "energy": "high",
    "genres": ["rock", "pop"]
  }
}
```

### GET /api/my/data/artists

Get user's artists from all sources (sync + quiz + manual), merged when same artist appears in multiple sources.

**MBID-first:** MusicBrainz ID is the primary identifier when available.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| page | int | 1 | Page number (1-indexed) |
| per_page | int | 100 | Artists per page (max 500) |

**Response:**
```json
{
  "artists": [
    {
      "mbid": "0383dadf-2a4e-4d10-a46a-e9e041da8eb3",
      "artist_name": "Queen",
      "sources": ["spotify", "lastfm"],
      "spotify_id": "1dfeR4HaWDbWqFHLkxsg1d",
      "spotify_rank": 1,
      "spotify_time_range": "medium_term",
      "lastfm_rank": 5,
      "lastfm_playcount": 847,
      "popularity": 90,
      "genres": ["rock", "glam rock"],
      "tags": ["classic rock", "british"],
      "is_excluded": false,
      "is_manual": false
    },
    {
      "mbid": null,
      "artist_name": "ABBA",
      "sources": ["quiz"],
      "spotify_id": null,
      "spotify_rank": null,
      "spotify_time_range": null,
      "lastfm_rank": null,
      "lastfm_playcount": null,
      "popularity": null,
      "genres": [],
      "tags": [],
      "is_excluded": false,
      "is_manual": true
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 100,
  "has_more": true
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| mbid | string\|null | MusicBrainz artist UUID (primary identifier) |
| artist_name | string | Artist name |
| spotify_id | string\|null | Spotify artist ID (for images/links) |
| tags | array | MusicBrainz community tags |

### POST /api/my/data/artists

Add an artist manually to user's preferences.

**MBID-first:** MusicBrainz ID is the primary identifier when available.

**Requires:** Bearer token

**Request:**
```json
{
  "artist_name": "Elton John",
  "mbid": "b83bc61f-8451-4a5d-8b8e-7e9ed295e822",
  "spotify_artist_id": "3PhoLpVuITZKcymswpck5b"
}
```

**Request Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| artist_name | string | Yes | Artist name to add |
| mbid | string | No | MusicBrainz artist UUID (primary identifier) |
| spotify_artist_id | string | No | Spotify artist ID for metadata enrichment |

If `mbid` is provided (from autocomplete), the artist will be stored with MusicBrainz metadata. If `spotify_artist_id` is also provided, it will be used for additional enrichment (images, popularity).

**Response:**
```json
{
  "artists": ["Existing Artist", "Elton John"],
  "added": "Elton John"
}
```

**Error Responses:**
- `400` - Artist already in user's data

### DELETE /api/my/data/artists/{artist_name}

Remove an artist from user's data (from all sources).

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artist_name | string | Artist name (URL encoded) |

**Response:**
```json
{
  "removed": "Queen",
  "removed_from": ["quiz", "spotify"],
  "success": true
}
```

### POST /api/my/data/artists/exclude

Exclude an artist from recommendations (soft hide). The artist remains in your data but won't be used when generating recommendations. Persists through re-syncs.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artist_name | string | Artist name to exclude |

**Response:**
```json
{
  "artist_name": "Taylor Swift",
  "excluded": true,
  "success": true
}
```

### DELETE /api/my/data/artists/exclude

Remove an artist from exclusions (un-hide). The artist will again be used when generating recommendations.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| artist_name | string | Artist name to include |

**Response:**
```json
{
  "artist_name": "Taylor Swift",
  "excluded": false,
  "success": true
}
```

### GET /api/my/data/preferences

Get user's quiz/recommendation preferences.

**Requires:** Bearer token

**Response:**
```json
{
  "decade_preference": "1980s",
  "energy_preference": "high",
  "genres": ["rock", "pop"]
}
```

### PUT /api/my/data/preferences

Update user's preferences.

**Requires:** Bearer token

**Request:**
```json
{
  "decade_preference": "1990s",
  "energy_preference": "medium",
  "genres": ["rock", "pop", "metal"]
}
```

All fields are optional. Use `null` to clear a preference.

**Response:**
```json
{
  "decade_preference": "1990s",
  "energy_preference": "medium",
  "genres": ["rock", "pop", "metal"]
}
```

---

## Known Songs ✅ Implemented

Endpoints for users to manually add songs they already know they like singing, independent of music service sync.

### GET /api/known-songs

Get user's known songs list.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| page | int | Page number (default: 1, min: 1) |
| per_page | int | Results per page (default: 20, max: 100) |

**Response:**
```json
{
  "songs": [
    {
      "id": "user123:1",
      "user_id": "user123",
      "song_id": "1",
      "source": "known_songs",
      "is_saved": true,
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "created_at": "2024-01-01T12:00:00Z",
      "updated_at": "2024-01-01T12:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20
}
```

### POST /api/known-songs

Add a song to user's known songs.

**Requires:** Bearer token

**Request:**
```json
{
  "song_id": 1
}
```

**Response (201 Created):**
```json
{
  "added": true,
  "song_id": "1",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "already_existed": false
}
```

**Error Responses:**
- `404` - Song not found in catalog

### POST /api/known-songs/bulk

Bulk add multiple songs to user's known songs.

**Requires:** Bearer token

**Request:**
```json
{
  "song_ids": [1, 2, 3]
}
```

**Response:**
```json
{
  "added": 2,
  "already_existed": 1,
  "not_found": 0,
  "total_requested": 3
}
```

### DELETE /api/known-songs/{song_id}

Remove a song from user's known songs.

**Requires:** Bearer token

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| song_id | int | Song ID to remove |

**Response:** `204 No Content`

**Error Responses:**
- `404` - Song not in user's known songs (or was added from different source)

**Note:** Only songs with source "known_songs" can be removed via this endpoint. Songs synced from Spotify/Last.fm use different endpoints.

### POST /api/known-songs/enjoy-singing

Mark a song as one the user enjoys singing at karaoke, with optional metadata.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| song_id | string | Song ID - karaoke catalog ID or "spotify:{track_id}" |

**Request:**
```json
{
  "singing_tags": ["crowd_pleaser", "shows_range"],
  "singing_energy": "emotional_powerhouse",
  "vocal_comfort": "challenging",
  "notes": "Great song for the finale!"
}
```

All fields are optional. Valid values:
- `singing_tags`: "easy_to_sing", "crowd_pleaser", "shows_range", "fun_lyrics", "nostalgic"
- `singing_energy`: "upbeat_party", "chill_ballad", "emotional_powerhouse"
- `vocal_comfort`: "easy", "comfortable", "challenging"
- `notes`: Free-form text (max 500 characters)

**Response (201 Created):**
```json
{
  "success": true,
  "song_id": "1",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "enjoy_singing": true,
  "singing_tags": ["crowd_pleaser", "shows_range"],
  "singing_energy": "emotional_powerhouse",
  "vocal_comfort": "challenging",
  "notes": "Great song for the finale!",
  "created_new": false
}
```

**Notes:**
- If the song already exists in user's library, updates it with enjoy_singing=true
- If the song doesn't exist, creates it with source="enjoy_singing"
- Works with both karaoke catalog IDs and Spotify track IDs (prefixed with "spotify:")

### DELETE /api/known-songs/enjoy-singing

Remove the enjoy singing flag from a song.

**Requires:** Bearer token

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| song_id | string | Song ID - karaoke catalog ID or "spotify:{track_id}" |

**Response:** `204 No Content`

**Notes:**
- If the song was added solely via enjoy_singing source, the song is deleted
- If the song exists from another source (Spotify sync, Last.fm, quiz), only the enjoy_singing metadata is cleared

---

## My Songs (Not Yet Implemented)

### GET /api/my/history

Get songs the user has sung.

### POST /api/my/songs/{song_id}/sung

Mark a song as sung with optional rating.

---

## Playlists (setlists) ✅ Implemented

A playlist is an ordered list of `items`. All routes need a Bearer token; another user's playlist is 403, an unknown id 404.

Playlist: `{id, name, description, song_ids, song_count, created_at, updated_at, items, rev}`.

- `items`: ordered `{item_id, song_key | null, artist, title, added_at, facets}`. `facets` is as in `/api/my/library`. `song_ids` and `song_count` are kept for older clients (the web app's legacy routes).
- `rev` increments on every write.
- Names are never filled in with the key: a keyed item with empty names is named from catalog titles when known (reads only), else `artist` and `title` stay empty and clients show `song_key`. A keyed item may have empty names; an unkeyed item needs artist and title (else 422).

### GET /api/playlists

`?limit=` (default 50, max 100) and `?offset=`. → `{playlists: [playlist], total}`.

### POST /api/playlists

`{name (1–100), description?, id?, items?}` → `201` playlist.

- `id` is a client-generated id matching `^pl_[A-Za-z0-9-]{8,64}$`. Creating is idempotent: posting an `id` that already belongs to the caller returns that playlist unchanged. An id that belongs to another user → 403.
- `items` (≤500) seeds the playlist (same item shape as `PUT /items`).

### GET /api/playlists/{playlist_id}

→ playlist.

### PUT /api/playlists/{playlist_id}

`{name?, description?}` → playlist (`rev` incremented). Items are untouched.

### PUT /api/playlists/{playlist_id}/items

`{items (≤500), base_rev?}` → playlist. Replaces the whole ordered list. Last writer wins: `base_rev` is accepted but a stale value does not reject the write. Each item is `{item_id?, song_key?, artist?, title?}` (`song_key` ≤600 chars, names ≤300). Names are stored as sent (the server does not merge in earlier names, so clients should echo them back); `item_id` is kept for an existing item (with its `added_at`), otherwise generated.

### DELETE /api/playlists/{playlist_id}

`204`.

### POST /api/playlists/{playlist_id}/songs

`{song_id}` → playlist. Legacy add (transactional): duplicates are ignored.

### DELETE /api/playlists/{playlist_id}/songs/{song_id:path}

`204`. Removes the song; the id is a path parameter and may contain `/`. Not in the playlist: no-op.

**Index:** listing queries `playlists` on `(user_id ASC, updated_at DESC)`, declared in Pulumi (`infrastructure/__main__.py`, imported from the existing index).

---

## Admin ✅ Implemented

Admin endpoints for system management. All endpoints require admin privileges (`is_admin: true` on user).

### GET /api/admin/stats

Get dashboard statistics.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "users": {
    "total": 150,
    "verified": 100,
    "guests": 50,
    "active_7d": 45
  },
  "sync_jobs_24h": {
    "pending": 0,
    "in_progress": 2,
    "completed": 25,
    "failed": 3
  },
  "services": {
    "spotify_connected": 85,
    "lastfm_connected": 30
  }
}
```

### GET /api/admin/users

List all users with pagination and filtering.

**Requires:** Bearer token (admin)

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Results per page (default: 20, max: 100) |
| offset | int | Number to skip (default: 0) |
| filter | string | "all", "verified", or "guests" |
| search | string | Search by email |

**Response:**
```json
{
  "users": [
    {
      "id": "user-uuid",
      "email": "user@example.com",
      "display_name": "John Doe",
      "is_guest": false,
      "is_admin": false,
      "created_at": "2024-12-30T12:00:00Z",
      "last_sync_at": "2024-12-30T14:00:00Z",
      "quiz_completed_at": "2024-12-30T12:30:00Z",
      "total_songs_known": 150
    }
  ],
  "total": 150
}
```

### GET /api/admin/users/{user_id}

Get detailed user information.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "id": "user-uuid",
  "email": "user@example.com",
  "display_name": "John Doe",
  "is_guest": false,
  "is_admin": false,
  "created_at": "2024-12-30T12:00:00Z",
  "last_sync_at": "2024-12-30T14:00:00Z",
  "quiz_completed_at": "2024-12-30T12:30:00Z",
  "total_songs_known": 150,
  "services": [
    {
      "service_type": "spotify",
      "service_username": "SpotifyUser123",
      "sync_status": "idle",
      "last_sync_at": "2024-12-30T14:00:00Z",
      "tracks_synced": 150,
      "sync_error": null
    }
  ],
  "sync_jobs": [
    {
      "id": "job-uuid",
      "status": "completed",
      "created_at": "2024-12-30T14:00:00Z",
      "completed_at": "2024-12-30T14:05:00Z",
      "error": null
    }
  ],
  "data_summary": {
    "artists_count": 50,
    "songs_count": 150,
    "playlists_count": 3
  }
}
```

### GET /api/admin/sync-jobs

List sync jobs with pagination and filtering.

**Requires:** Bearer token (admin)

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Results per page (default: 20, max: 100) |
| offset | int | Number to skip (default: 0) |
| status | string | "all", "pending", "in_progress", "completed", or "failed" |

**Response:**
```json
{
  "jobs": [
    {
      "id": "job-uuid",
      "user_id": "user-uuid",
      "user_email": "user@example.com",
      "status": "completed",
      "created_at": "2024-12-30T14:00:00Z",
      "completed_at": "2024-12-30T14:05:00Z",
      "error": null
    }
  ],
  "total": 100
}
```

### GET /api/admin/sync-jobs/{job_id}

Get detailed sync job information.

**Requires:** Bearer token (admin)

**Response:**
```json
{
  "id": "job-uuid",
  "user_id": "user-uuid",
  "user_email": "user@example.com",
  "status": "completed",
  "created_at": "2024-12-30T14:00:00Z",
  "completed_at": "2024-12-30T14:05:00Z",
  "error": null,
  "progress": {
    "current_service": null,
    "current_phase": null,
    "total_tracks": 500,
    "processed_tracks": 500,
    "matched_tracks": 350,
    "percentage": 100
  },
  "results": [
    {
      "service_type": "spotify",
      "tracks_fetched": 500,
      "tracks_matched": 350,
      "user_songs_created": 200,
      "user_songs_updated": 150,
      "artists_stored": 75,
      "error": null
    }
  ]
}
```

**Error Responses:**
- `403` - Admin access required
- `404` - Job not found

### POST /api/admin/impersonate

Generate a JWT token to impersonate a specific user. Useful for debugging user-reported issues by viewing the UI as they see it.

**Requires:** Bearer token (admin)

**Request:**
```json
{
  "user_id": "user_abc123",  // OR
  "email": "user@example.com"
}
```

At least one of `user_id` or `email` must be provided.

**Response:**
```json
{
  "token": "eyJ...",
  "expires_in": 604800,
  "user_id": "user_abc123",
  "user_email": "user@example.com",
  "user_display_name": "John Doe"
}
```

**Notes:**
- Guest users receive a guest token (30-day expiry)
- Verified users receive a standard token (7-day expiry)
- Impersonation is logged for audit purposes

**Error Responses:**
- `400` - Must provide either user_id or email
- `403` - Admin access required
- `404` - User not found

---

## Voice session ✅ Implemented

Phase 3d. A guided singing session that finds the singer's voice type and key-fit from a few songs. All `/api/voice/*` endpoints need a signed-in user.

**Gate:** session endpoints (`/api/voice/sessions*`, `POST /api/voice/recordings`) return `403 voice_session_disabled` unless `VOICE_SESSION_OPEN` is on or the user is an admin. `503 warming_up` (voice.db or the catalog not loaded yet) can come from any of them. Request bodies reject unknown keys (`422`); there is no audio or raw pitch frame in any session body.

### GET /api/voice/flags

`{"session_enabled": true, "is_admin": false, "voice_data": true, "active_session_id": "abc123" | null}`. Never gated. `active_session_id` is the user's active session (the newest, should two ever be active), so the app can offer to resume it with `GET /api/voice/sessions/{session_id}`.

### POST /api/voice/sessions

Body `{"posture": "standing|sitting|lying", "warmth": "cold|bit_warm|warm", "record": false, "headphones": true, "device": "Pixel", "experience": "none|some|trained", "uses_falsetto": "yes|sometimes|no|unsure"}` (`device` optional, max 80; `experience` and `uses_falsetto`, the optional "About your singing" answers, since v0.33.0: stored in the setup only when given, read back as `null` otherwise, and copied into the session's voice profile). Abandons the user's other active session. Returns `{"session_id": "...", "state": "active"}`.

### GET /api/voice/sessions/{session_id}

The session as it stands, for resuming it. Read-only: never changes the session. Errors: `404 session_not_found` (also for another user's session).

```json
{"session_id": "abc123", "state": "active|abandoned|done",
 "setup": {"posture": "standing", "warmth": "warm", "record": false, "headphones": true, "device": "Pixel",
           "experience": null, "uses_falsetto": null},
 "attempts": 2, "top_p": 0.41, "done_reason": null, "last_offer": {"kind": "song", "song_key": "artist|title", "...": "..."},
 "profile_saved": false}
```

`last_offer` is the song to show (the `GET .../next` shape, `attempt_n` recomputed): the last offered song while the session is active and not done, that song is neither attempted nor finished by a skip, and its version was not reported `not_embeddable` since; otherwise `null` (the app then calls `GET .../next`). `done_reason` is set once the session is over (or would end on the next `next`). `setup.record` reads `false` after the user deleted their recordings (see `DELETE /api/voice/recordings`).

### PATCH /api/voice/sessions/{session_id}

Any subset of the setup body above (the mid-session context chip). `204`. Errors: `404 session_not_found` (also for another user's session), `409 session_not_active`.

### GET /api/voice/sessions/{session_id}/next

The next song, or the end of the session. Errors: `404 session_not_found`, `409 session_not_active`.

```json
{"kind": "song", "song_key": "artist|title", "artist": "Artist", "title": "Title", "duration_s": 215,
 "reason_type": "explore", "version": {"video_id": "FIXTURE11", "brand_code": "SING", "brand_name": "Sing King", "embeddable": true},
 "alternatives": [], "song_range": {"low": 48, "high": 70, "p10": 52, "p50": 59, "p90": 66},
 "attempt_n": 3, "max_attempts": 12,
 "comfort_estimate": {"low": 49, "high": 59, "sd_high": 1.4, "source": "measured|model|profile"}}
```

or `{"kind": "done", "reason": "..."}`. `attempt_n` is the 1-based number an attempt on this song would get, out of `max_attempts` (12). `version.embeddable` is `false` only for the link-out offer (no embeddable version is left): the app opens YouTube instead of embedding. `alternatives` holds at most 2 versions; `song_range` is `null` without data.

`comfort_estimate` (since v0.33.0) is the session's chest comfort band when the song is offered, the reference for the app's live "High note: falsetto?" chip: `measured` once the songs rated comfortable measure it (the band the result would show), else `profile` while no attempt counts yet and the session's prior profile (frozen at setup) has a band (its own `comfort_low`/`comfort_high`), else `model` (the posterior mean, one decimal). `sd_high` is always the model's posterior SD of the comfort top. Excluded attempts don't count. It is stored with the offer, so `GET /api/voice/sessions/{id}` `last_offer` repeats it.

### POST /api/voice/sessions/{session_id}/skips

Body `{"song_key": "...", "reason": "dont_know|something_else|not_embeddable", "video_id": null}`. `not_embeddable` (with `video_id`) marks that version unplayable for this user (in this and later sessions); it becomes unplayable for everyone once 2 distinct users have reported it (`voice_version_stats/{video_id}.not_embeddable_by`). A repeat report by the same user counts once. A guest account's report hides the version for that guest only and never counts toward the 2. `204`. A song (and, for `not_embeddable`, a version) that the last `next` offered is accepted even if a new voice.db no longer has it (the offer's snapshot is used, and the song is finished for the session). Errors: `404 session_not_found`, `409 session_not_active`, `422` for an unknown song or version (`unknown_song`, `unknown_version`).

### POST /api/voice/sessions/{session_id}/attempts

One sung song. Body: `song_key`, `video_id`, `started_at`, `ended_at` (>= `started_at`), `stopped_early`, `quiz`, an optional `pitch` summary and (since v0.33.0, both optional) `pitch_all` and `note_labels`.

```json
{"song_key": "artist|title", "video_id": "FIXTURE11", "started_at": "2026-10-22T19:00:00Z", "ended_at": "2026-10-22T19:03:00Z",
 "stopped_early": false, "pitch": null, "pitch_all": null,
 "note_labels": [{"t_start": 151.2, "t_end": 152.0, "midi": 69.0, "label": "falsetto|full|unsure", "source": "live|quiz"}],
 "quiz": {"feel": "loved|fine|not_for_me", "position": "too_low|comfortable|too_high|both_ends",
          "difficulty": "easy|stretch|couldnt", "knew": "well|partly|barely", "would_sing": "yes|yes_other_key|no",
          "would_work_if": ["warmer", "other_key", "other_octave", "practice", "fine"], "voice_now": "fresh|warm|tired",
          "version_quality": "good|not_great", "version_issues": ["no_intro", "wrong_lyrics", "bad_sync", "low_quality"],
          "tags": ["fast_lyrics", "long_gaps", "needed_warmup", "crowd_song", "mood_only"]}}
```

`feel` and `position` are required; the other quiz fields are optional. `pitch` is `{voiced_s, held_count, held_low, held_high, p10, p50, p90}` in MIDI numbers (24-96, `held_low <= held_high`, `p10 <= p50 <= p90`).

**Falsetto (since v0.33.0).** `pitch` is the **chest-only** summary: the app removes the spans the singer labelled falsetto before summarising. `pitch_all` (same shape, optional) is the unfiltered summary, stored for analysis only. `note_labels` (optional, at most 40) are the singer's answers about held high notes: `t_start`/`t_end` in seconds on the take's stream clock (0-3600, `t_end >= t_start`), `midi` the held note (24-96), `label` `falsetto|full|unsure`, `source` `live` (the chip while singing) or `quiz` (the catch-up). Old apps send an unfiltered `pitch` and neither field, which stays valid. When `note_labels` has falsetto notes but `pitch_all` is absent, the model can't trust `pitch` to be chest-only, so it caps `held_high` 1 semitone below the lowest falsetto-labelled note (if every held note was falsetto, it ignores the held notes instead). Returns `{"attempt_id": "<session>:<n>", "n": 1, "done": false, "done_reason": null, "top_p": 0.4}`. Each attempt also writes (once) the song's `user_songs` row (`source: voice_session` on a new row, `session_*` labels, `times_sung` + 1), version stats, and a "Love it" for `feel: loved`. Errors: `404 session_not_found`, `409 session_not_active`, `409 max_attempts` (12 per session), `422` validation, `unknown_song`, `unknown_version`. The song and version the last `next` offered are always accepted, even after a voice.db swap dropped them (the attempt stores the offer's snapshot of the song).

### GET /api/voice/sessions/{session_id}/result

Works in any session state (no attempts yet: the prior, `partial: true`). On an **active** session it marks the session done and saves the voice profile (`method: session`) once the result is confident enough; a **done** session returns its stored `profile_saved` again; an **abandoned** session (the user started a newer one) gets its result with `profile_saved: false`, never saves over the newer profile, and stays abandoned. Errors: `404 session_not_found`. A failure writing an attempt's outputs never fails the result (logged, retried on the next fetch).

```json
{"voice_type": "tenor", "between": null, "confidence_label": "low", "top_p": 0.5, "low": 45, "high": 69,
 "comfort_low": 49, "comfort_high": 59, "comfort_source": "measured", "falsetto_high": 69, "attempts": 3, "partial": false, "improve_hint": null, "done_reason": "confident", "profile_saved": false,
 "suited": [], "loved": [{"song_key": "artist|title", "artist": "Artist", "title": "Title"}]}
```

`falsetto_high` (since v0.33.0; `null` without one) is the highest note labelled falsetto (`note_labels`, rounded) on an attempt that counts: excluded attempts' labels don't. Falsetto notes are kept out of the chest range: `low`/`high` and the comfort come from the chest-only `pitch` the app sends with `pitch_all`. For an attempt with labels but no `pitch_all` (an older app), only `held_high` is capped below the lowest falsetto label; its percentiles are kept, so the tessitura evidence can still include falsetto notes there.

`comfort_source` (since v0.32.0; default `model`) says where `comfort_low`/`comfort_high` come from: `measured` once at least 2 songs rated comfortable and easy, known better than "barely", carry a usable `pitch` (60 s voiced in total): the voiced-seconds-weighted mean of their sung p10/p90 (each kept inside that song's held notes), widened to at least 11 keys around its centre and to cover each of those songs' p10..p90, shifted inside `low`..`high`; `model` otherwise: the voice model's estimate for the likely types. A saved `method: session` profile stores the same comfort. `confidence_label` is `medium` ("between") only when the top type has at least 0.7 within the first 5 counting songs (excluded ones don't count) (any settled adjacent pair after that), and a measured comfort counts as settled (see `docs/VOICE-SESSION-TUNING.md`).

Every call (finish, refetch, an abandoned session) stores the result as the session's `result_summary` (the `GET /api/voice/history` `result`, with `evidence_attempts` and `computed_at`). `suited` is up to 15 songs in the recommendations v2 shape, ranked by your taste (recommendations v2 `sort=best` with `singable=true`, so songs by artists you know and love come first) and topped up from `sort=fit` when taste gives fewer than 5; `loved` the songs marked "Love it" in this session. `done_reason` (`confident|tired|max_attempts|no_candidates`, or `null` when the singer finished early) is why the session ended. `method: session` profiles are written only here: `PUT /api/my/voice` rejects them (`422`).

### POST /api/voice/sessions/{session_id}/adopt

"Use this result": saves the session's result as your voice profile (`method: session`) even at low confidence, and marks the session's result `profile_saved: true`. Returns the saved profile (the `GET /api/my/voice` `profile` shape). Your mic result stays as `mic`. Gated like the other session endpoints. Errors: `404 session_not_found` (unknown, or another user's), `409 no_evidence` (no counting attempt with range evidence, so there is nothing to save), `404 user_not_found` (no profile doc).

### GET /api/voice/history

Your voice data, for "Your recordings" on Me: the signed-in user's sessions that have at least one sung song, newest first, at most 20. Open to every signed-in user, even with the session flag off.

```json
{"sessions": [{"session_id": "abc123", "created_at": "2026-10-25T19:00:00Z", "state": "done",
  "result": {"session_id": "abc123", "voice_type": "baritone", "between": null, "confidence_label": "low", "top_p": 0.5,
             "low": 45, "high": 69, "comfort_low": 50, "comfort_high": 61, "comfort_source": "model", "falsetto_high": null, "attempts": 4, "evidence_attempts": 4,
             "profile_saved": false, "computed_at": "2026-10-25T19:40:00Z"},
  "attempts": [{"attempt_id": "abc123:1", "n": 1, "song_key": "artist|title", "artist": "Artist", "title": "Title",
                "sung_at": "2026-10-25T19:08:00Z", "feel": "loved", "position": "comfortable", "excluded": false,
                "recording": {"recording_id": "abc123:1", "duration_s": 180.5, "content_type": "audio/mp4", "bytes": 48000},
                "falsetto_notes": 0}]}]}
```

`result` is the session's result as last computed (`null` before the first `GET .../result`). Its `comfort_source` is the result's (`measured` / `model`; summaries stored before v0.32.0 read as `model`), and a "Don't count this one" recompute updates it: excluding a comfortable song can leave too few to measure. `recording` is `null` when nothing was recorded, the recording was deleted, or the upload hasn't arrived yet (the app shows its own queued takes as "Uploading…"). `excluded` is "Don't count this one" (`PATCH /api/voice/attempts/{attempt_id}`). `falsetto_notes` is how many of the attempt's `note_labels` say falsetto; the `result`'s `falsetto_high` is as in `GET .../result`.

### PATCH /api/voice/attempts/{attempt_id}

"Don't count this one". Body `{"excluded": true}` (or `false` to count it again; unknown keys `422`). The attempt stays in the history and its song is still never offered again in that session, but it leaves the voice model: it no longer moves the result, counts as range evidence or as a "tired" answer. The session's result is then recomputed (stored as its `result` for a finished session; an active session's next `GET .../next` and `GET .../sessions/{id}` use it at once), and if your saved voice profile came from this session it is re-derived:

```json
{"attempt_id": "abc123:2", "excluded": true,
 "result": {"session_id": "abc123", "voice_type": "baritone", "...": "...", "evidence_attempts": 1, "profile_saved": true},
 "profile": "updated"}
```

`profile`: `updated` (saved again from the attempts that still count, whatever its confidence), `reverted_to_mic` (no evidence left: your last mic-test result is your profile again), `removed` (no evidence left and no mic result), or `unchanged` (the saved profile isn't this session's). Counting the attempt again after `reverted_to_mic` / `removed` saves the session's profile again (`updated`), unless you have chosen another profile since. A mic result saved before v0.31.0 (no kept copy yet) is copied aside the first time a session or adopt saves over it, so it is never lost. Song feedback (`user_songs`) is untouched. Open to every signed-in user, even with the session flag off. Errors: `404 attempt_not_found` (unknown, or another user's).

### POST /api/voice/recordings

Private, consent-gated audio of one attempt (multipart form: `file`, `session_id`, `attempt_n` 1-12, `duration_s`, optional `device`; other fields `422 unknown_form_fields`). Audio is `audio/mp4` or `audio/wav`, at most 20 MiB. Returns `201 {"recording_id": "..."}`. Errors: `403 voice_session_disabled`, `403 recording_not_consented` (the session setup had `record: false`), `403 session_forbidden` (not the user's session), `404 attempt_not_found`, `409 recordings_deleted` (the user deleted their recordings while this upload ran; nothing was kept), `409 recording_deleted` (the user deleted this take's recording; nothing was kept), `413 recording_too_large` (checked before the whole body is read), `415 unsupported_media_type`, `422 empty_recording` or form validation.

### DELETE /api/voice/recordings

Erases every recording of the signed-in user (objects, documents, attempt links) and withdraws recording consent (`setup.record = false`) on all their sessions, so an upload queued before the delete is refused with `403 recording_not_consented`; the user re-consents with `PATCH` (`{"record": true}`). Idempotent. Returns `{"deleted": 2}`. Open to every signed-in user, even with the session flag off.

### DELETE /api/voice/recordings/{recording_id}

Deletes one of your recordings for good: the stored object (real erasure), its document and the attempt's link. `204`, and a repeat call is `204` too. Another user's recording or an unknown id: `404 recording_not_found`. Consent for the session's other takes is unchanged. Afterwards an upload of that take is refused with `409 recording_deleted` (drop it from the queue), and an upload that was already in flight undoes itself and gets the same 409. Open to every signed-in user, even with the session flag off.

### GET /api/voice/recordings/{recording_id}/audio

Streams one of your own recordings with its stored content type (`audio/mp4` or `audio/wav`). No Range support: the app downloads the file, then plays it. Open to every signed-in user, even with the session flag off. `404 recording_not_found` when the recording doesn't exist, belongs to someone else (never `403`), or its object is gone; a stored path outside your own `voice-recordings/<user_id>/` prefix is also a 404 (logged as a WARNING). Admin playback (`/api/admin/voice/recordings/{id}/audio`) is unchanged.

### GET /api/voice/recordings/count

`{"count": 2}`. Open to every signed-in user.

### App upload queue rules

The app queues recordings and uploads them in the background. For each queued upload:

- **Drop it** on `403 recording_not_consented`, `409 recordings_deleted`, `409 recording_deleted`, or `404 attempt_not_found`: the server will never take it.
- **Keep it** (retry later) on `5xx` and on network errors.
- **Before calling `DELETE /api/voice/recordings`, clear the queue**, so nothing recorded before the delete is sent after it. (The server also refuses such uploads, since the delete withdraws consent, but the app should not rely on that.)

### Personal pool ("analyse my music")

Behind the session gate (`403 voice_session_disabled` while `VOICE_SESSION_OPEN` is off, unless admin). An eligible user asks for up to 20 of their best-known songs (by taste weight, then playcount) to be fetched and analysed; personal songs are downloaded only once no shared-pool song is waiting, so they typically land overnight. Analysed songs join the shared pool for everyone; the pool rows carry `source = personal`, never the user.

- **Eligible** (`reason`): `admin`; `paid` (the gen account has `total_spent > 0` or a purchase credit transaction: `stripe_purchase`, `iap_purchase` or legacy `purchase`); `history` (≥ 1,000 listens summed over `user_songs.playcount`, or ≥ 200 songs played at least once); otherwise `not_eligible`. Guests are never eligible.
- **Picks**: known songs with a community karaoke version, not hidden or marked "would not sing", not already in voice.db and not already in the fetch queue (any source, any status).
- **Cap**: 20 songs per user per rolling 30 days (`VOICE_PERSONAL_CAP`, `VOICE_PERSONAL_WINDOW_DAYS`); a request is trimmed to what is left. Admins are exempt.
- **Budget**: a global monthly (UTC) budget on the estimated spend, `VOICE_MONTHLY_BUDGET_USD` (20.0) at `VOICE_EST_COST_PER_SONG_USD` (0.027) per newly queued song. A request is trimmed to the songs the budget can still take; admins are not exempt.

#### GET /api/voice/personal-pool

```json
{
  "eligible": true,
  "reason": "paid | history | admin | not_eligible",
  "listens": 1320,
  "songs_with_plays": 8,
  "next_allowed_at": "2026-11-20T19:04:11.123456Z" | null,
  "cap": 20,
  "used": 20,
  "batches": [
    {"batch_id": "4f1c…", "requested_at": "2026-10-21T19:04:11.123456Z", "total": 20, "ready": 12, "status": "queued | running | done"}
  ]
}
```

`used` is the songs requested in the last 30 days. `next_allowed_at` is set only while nothing is left (when enough of the oldest batches leave the window for one more song); always `null` for admins. `batches` are newest first. `ready` counts songs analysed and present in the voice.db the API has loaded (the session can offer them). `status`: `done` once every song is analysed or failed, `running` once any is past queued, else `queued`.

#### POST /api/voice/personal-pool

No body. `201` → `{"batch_id": "4f1c…", "songs": ["abba|dancing queen", "…"], "est_cost_usd": 0.54}`. `songs` are the batch's song keys; `est_cost_usd` covers the songs this request newly queued (a song another batch queued a moment earlier is shared, not charged again).

Refusals (`detail` is an object):

| Status | `detail` | Meaning |
|---|---|---|
| `403` | `{"reason": "not_eligible"}` | See eligibility above |
| `429` | `{"reason": "cap", "next_allowed_at": "2026-11-20T19:04:11.123456+00:00"}` | No songs left in the window |
| `409` | `{"reason": "nothing_to_add"}` | No known song qualifies |
| `409` | `{"reason": "concurrent_request"}` | Another request of yours kept committing first; retry |
| `503` | `{"reason": "budget_paused"}` | This month's budget can't take a single new song; resumes next month |

`403 voice_session_disabled` and `503 warming_up` (catalog not loaded) keep their plain-string `detail`.

### Admin voice (`/api/admin/voice/*`)

Admin session or `X-Admin-Token` (otherwise `403`).

- `POST /api/admin/voice/labels/import` body `{"email", "rows": [{"artist", "title", "duet", "good", "sung", "notes"}], "enqueue": true}` (max 500 rows). Stores the sheet rows as per-user `voice_labels` (idempotent per user + song) and queues matched songs for analysis. Returns `{"imported", "matched", "unmatched": [{"artist","title"}], "enqueued"}`. `503 warming_up` without the catalog.
- `GET /api/admin/voice/coverage` -> `{"run", "songs", "analysed", "per_stratum_dimension": {dim: {value: {songs, analysed}}}, "queue": {status: n}, "budget": {...} | null}` (`budget` is flacfetch's Spotify budget, `null` when unavailable).
- `GET /api/admin/voice/users/{email}/songs` -> every song the user labelled or attempted: `[{"song_key", "artist", "title", "markers", "label": {"good","tags","notes"} | null, "attempts": [{"session_id","at","quiz","context","pitch","recording_id"}], "prediction": {"good","p"} | null}]` (`prediction` is the good/no prediction from `karaoke_decide/voice_session/predict.py`; `null` without a voice profile or an analysis row). `404 user_not_found`.
- `GET /api/admin/voice/agreement?email=` -> the prediction vs the user's yes/no labels on held-out songs (labelled yes/no, analysed, never attempted in a session): `{"n", "agree", "rate", "confusion": {"tp","fp","tn","fn"}, "ready_for_signoff", "per_marker": {feature: {"agree","disagree"}}, "weights", "skipped_no_profile", "skipped_no_range", "rows": [{"song_key","artist","title","label","features","p","pred"}]}`. `ready_for_signoff` = `n >= 30 and rate >= 0.80` (Andrew's OK is still required). `per_marker` is the mean `w·x` per feature over agreeing vs disagreeing rows. `scripts/voice/agreement.py` prints it and replays exported rows offline with trial `--weights`. `404 user_not_found`, `503 warming_up` without voice.db.
- `GET /api/admin/voice/costs` -> `{"month": "2026-10", "personal_batches", "personal_songs", "est_cost_usd", "budget_usd", "paused", "shared_songs_analysed", "est_cost_per_song_usd"}`: this UTC month's personal-pool batches, newly queued songs and estimated spend; `paused` when not one more song fits in `budget_usd` (requests then get `503 budget_paused`); `shared_songs_analysed` counts analysed shared + sheet songs (all time).
- `GET /api/admin/voice/recordings/{recording_id}/audio` streams the private recording with its stored content type. `404 recording_not_found`.

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message here"
}
```

Common status codes:
- `400` - Bad request (invalid input)
- `401` - Not authenticated
- `403` - Not authorized
- `404` - Resource not found
- `429` - Rate limited
- `500` - Internal server error
- `501` - Not implemented
