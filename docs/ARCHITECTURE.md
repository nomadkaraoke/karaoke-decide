# Architecture

## System Overview

Nomad Karaoke Decide is a system that helps users discover karaoke songs based on their music listening history.

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Clients                                    │
├─────────────────┬─────────────────┬─────────────────────────────────┤
│    CLI Tool     │    Web App      │           Mobile App            │
│  (karaoke-     │  (Next.js @     │          (Future)               │
│   decide)      │  decide.        │                                 │
│                │  nomadkaraoke   │                                 │
│                │  .com)          │                                 │
└────────┬───────┴────────┬────────┴─────────────────────────────────┘
         │                │
         │                ▼
         │   ┌────────────────────────┐
         │   │   Cloudflare Worker    │  (API Proxy)
         │   │   /api/* → Cloud Run   │
         │   └───────────┬────────────┘
         │               │
         └───────┬───────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Backend API                                   │
│                    (FastAPI on Cloud Run)                           │
├─────────────────────────────────────────────────────────────────────┤
│  /api/catalog/*   - Karaoke song catalog (BigQuery)        ✅ LIVE  │
│  /api/auth/*      - Gen session + legacy JWT auth                ✅ LIVE  │
│  /api/services/*  - Music service OAuth & sync             ✅ LIVE  │
│  /api/my/*        - User data, recommendations             ✅ LIVE  │
│  /api/playlists/* - Playlist management                    ✅ LIVE  │
│  /api/quiz/*      - Onboarding quiz                        ✅ LIVE  │
└────────┬───────────────────┬────────────────────────────────────────┘
         │                   │
         ▼                   ▼
┌─────────────────┐ ┌─────────────────────────────────────────────────┐
│   BigQuery      │ │              External Services                  │
│   (Analytics)   │ ├─────────────────────────────────────────────────┤
├─────────────────┤ │  Spotify API     - User listening history       │
│ karaokenerds_   │ │  Last.fm API     - Scrobbles and loved tracks   │
│   raw (275K)    │ │  ListenBrainz    - Similar artist data          │
│ spotify_tracks  │ │  KaraokeNerds    - Karaoke song catalog         │
│   (256M)        │ │  Postmark        - Magic link emails            │
├─────────────────┤ └─────────────────────────────────────────────────┘
│   Firestore     │
│   (User Data)   │
├─────────────────┤
│  decide_users   │
│  music_services │
│  user_songs     │
│  playlists      │
└─────────────────┘
```

## Cloudflare Worker (API Proxy)

The web frontend at `decide.nomadkaraoke.com` uses a Cloudflare Worker to proxy API requests to Cloud Run. This eliminates CORS issues by keeping frontend and API on the same origin.

```
Browser → decide.nomadkaraoke.com/api/* → Cloudflare Worker → Cloud Run
       → decide.nomadkaraoke.com/*      → GitHub Pages
```

**Why?**
- No CORS configuration needed (same-origin requests)
- Cloud Run URL not exposed to users
- Can add edge caching/rate limiting in future

The same Worker also routes `/api/search` and `/api/search/*` to the search service (`SEARCH_BACKEND_URL`), passing the client IP in a non-cf `X-Nomad-Client-IP` header (Cloudflare can overwrite `CF-Connecting-IP` on the subrequest to run.app) for its per-IP rate limit; the Worker overwrites any client-supplied value, and the backend prefers it over `CF-Connecting-IP`, then the socket peer. It also serves `decide.nomadkaraoke.com/offline-index/<run-token>/offline.db.gz` as an edge-cached (7-day) passthrough to the public `nomadkaraoke-song-index-public` bucket. Only that exact path shape is fetched; anything else under `/offline-index/` is a 404. See [Song search](#song-search).

**Configuration:** Managed via Pulumi in `infrastructure/__main__.py`, which deploys `infrastructure/cloudflare-worker/api-proxy.js` as-is. Routing tests: `infrastructure/cloudflare-worker/api-proxy.test.mjs`. See `infrastructure/cloudflare-worker/README.md` for setup.

## Song search

The Nomad Karaoke app's song search runs on a separate scale-to-zero Cloud Run service. It uses the same image as the API, with a different command (`uvicorn backend.search.app:search_app`).

```
App → decide.nomadkaraoke.com/api/search* → Worker → karaoke-decide-search (Cloud Run)
App → decide.nomadkaraoke.com/offline-index/<run-token>/offline.db.gz
                                          → Worker (edge cache) → gs://nomadkaraoke-song-index-public
song-index-build (Cloud Run Job, daily 13:30 UTC)
    gs://nomadkaraoke-kn-data/song-id/ + full/ + community/   (written by gen's kn-data-sync)
    → song_id.db, availability.db → gs://nomadkaraoke-kn-data/song-index/<run>-<token>/
    → offline.db.gz               → gs://nomadkaraoke-song-index-public/<run>-<token>/
    → eval gates → sets SONG_INDEX_RUN=<run>-<token> on the service (new revision)
    → song-index/latest.json → prune (keeps live runs + the previous 3)
```

| Resource | Settings |
|----------|----------|
| `karaoke-decide-search` service | 1 vCPU, 4 GiB, min 0 / max 3 instances, concurrency 80, CPU only during requests (`cpu_idle`), startup CPU boost. Runs as SA `song-search` (objectViewer on kn-data, secretAccessor on `decide-gen-partner-secret`). Unauthenticated. |
| Startup probe | `GET /api/search/ready` every 10 s, 24 tries (240 s, Cloud Run's maximum). uvicorn listens only once the index has loaded, and `/ready` is 503 if the load failed, so a revision without an index never takes traffic. |
| `song-index-build` job | 2 vCPU, 8 GiB (the in-memory disk shares it), timeout 1 h, no retries. Runs as SA `song-index-build`: objectViewer on kn-data plus objectAdmin limited to `song-index/` by an IAM condition; objectAdmin on the public bucket; `run.developer` on the search service only; project `run.viewer` (to read revisions); actAs on `song-search`; `artifactregistry.reader` on `karaoke-repo` (Cloud Run makes the deployer pull the image). |
| `song-index-build-daily` scheduler | `30 13 * * *` UTC. This is after kn-data-sync. A run whose source is unchanged exits after reading two JSON files. |
| `nomadkaraoke-song-index-public` bucket | UBLA, with `allUsers` → `roles/storage.legacyObjectReader`: objects can be read by exact name but not listed. Run dirs carry an unguessable 16-hex token. No age-based lifecycle (the served file may be any age); the job prunes. No soft delete. |
| `search-health-uptime` uptime check | `GET https://decide.nomadkaraoke.com/api/search/health` every 60 s from 3 US locations (the minimum). It keeps an instance warm and exercises the Worker route. There is deliberately no alert policy on it. |

**Who owns what.**
- The settings live in both Pulumi and the CI deploy step (`gcloud run deploy karaoke-decide-search`). `tests/unit/test_infra_ci_parity.py` fails if the two disagree.
- `SONG_INDEX_RUN` belongs only to the build job:
  - Pulumi never declares it and `ignore_changes` the container env and image.
  - CI uses `--update-env-vars` / `--update-secrets`, which merge into the live env, and never `--set-env-vars`.
  - When `SONG_INDEX_RUN` is absent or empty, the service loads `song-index/latest.json`.
- The `decide-gen-partner-secret` secret is created by karaoke-gen's Pulumi. Decide only grants access to it and mounts it as `DECIDE_GEN_PARTNER_SECRET`.

### Search ops notes

- **Eval gates** (`gate_failures` in `karaoke_decide/etl/song_index_build.py`). A build publishes only if all of these hold:
  - **Cloud eval** (labelled set): `wrong_auto` rises by at most 1, and the `auto` rate drops by at most 2 points, compared with the previous manifest.
  - **Dual eval** (offline `offline.db` with margin bonus vs cloud `song_id.db`, over the labelled set plus 900 frozen synthetic cases). "Same song" means the matcher's own `identify.same_song`: the same base title (version suffixes such as "(live)" or "- Remastered" are dropped) and a credited artist in common ("A & B" = "A feat. B"). Three checks:
    - `confident_disagree` (both sides confident, on different songs) must be at most `DUAL_EVAL_MAX_DISAGREE` (1%) of cases.
    - `offline_confident_cloud_unsure_other` (offline is sure, the cloud is unsure and its best guess is another song or nothing) must be at most `DUAL_EVAL_MAX_UNSURE_OTHER` (2%) of cases. This is a loose backstop. The first prod build had 9/1023 (0.9%).
    - `offline_wrong_auto` must be at most `cloud_wrong_auto + 2`. Both use `run_eval`'s confident-WRONG over every case: a confident answer that isn't the label (version suffixes are stripped, no fuzzy title match), or any confident answer on a negative (unlabelled) case.
  - Reported but not gated: `offline_more_confident` (offline is sure of the cloud's own unsure best guess) and `offline_downgraded` (the cloud is sure, offline asks).
  - The manifest's `dual_eval.disagreements` holds up to 100 entries, gated class first. They are also logged as `jsonPayload.dual_eval_disagreements`. `wrong_auto_cases` lists the label misses, offline-wrong first, capped at 100.
- **Alerts.** A failed build, a failed eval gate or a failed rollout logs exactly one ERROR, which pages #alerts through the error monitor. The served index is untouched, and tomorrow's run retries. There are no other alert policies.
- **Force a rebuild** (same source; for example after a matcher version change that you don't want to wait a day for):
  `gcloud run jobs execute song-index-build --region us-central1 --args=run,--force`
- **Manual rollback = pin traffic** to the previous good revision:
  `gcloud run services update-traffic karaoke-decide-search --region us-central1 --to-revisions=<good-revision>=100`
  - While traffic is pinned, each daily build still uploads its run and creates a revision, but that revision gets no traffic. The job logs one ERROR ("traffic is pinned") and skips `latest.json` and pruning.
  - Unserved run dirs therefore accumulate, roughly one per day of pinning, in both buckets. The next unpinned run prunes them (it keeps only the live runs plus the previous 3).
  - Pulumi ignores `traffics` on this service, so neither `pulumi refresh` nor `pulumi up` un-pins a rollback. CI's `gcloud run deploy` doesn't move pinned traffic either.
  - To undo: `gcloud run services update-traffic karaoke-decide-search --region us-central1 --to-latest`. The next build then prunes.
- **Stale source.** If kn-data-sync stops producing new runs, the service keeps serving the last index, and the offline file stays downloadable: nothing deletes a served run by age. Fix the sync, or force a rebuild.
- **Changing the service's env or settings.** Edit both `infrastructure/__main__.py` and the CI step (the parity test enforces this).
  - Env changes reach the live service through CI's `--update-env-vars`, because Pulumi ignores env after creation.
  - Before a `pulumi up` that changes this service, run `pulumi refresh`. Otherwise Pulumi sends the stale env from its state, which drops `SONG_INDEX_RUN`. The new revision would then load `latest.json`, which is the same index unless a build's rollout was pinned or failed. Traffic is never changed by Pulumi (`traffics` is ignored).
- **First rollout (bootstrap).** The service can't pass `/ready` until an index exists. So:
  1. Run `pulumi up -c searchServiceEnabled=false`. This creates everything except the service.
  2. Run the job once with `--no-deploy` immediately after phase 1, before the scheduler's next 13:30 UTC run (or first pause it: `gcloud scheduler jobs pause song-index-build-scheduler --location us-central1`, and resume afterwards). It publishes and writes `latest.json` without a rollout; a scheduled run before the service exists would try a rollout and fail.
  3. Run `pulumi config rm searchServiceEnabled && pulumi up`.

  Until the service exists, CI skips its search deploy and job-image steps with a warning.

## Recommendations catalog (catalog.db) and startup

Recommendations v2 (`GET /api/my/recommendations/v2`) reads two SQLite files, never BigQuery:

- **`catalog.db`**: per-song facets (genre, mood, decade, energy, lead gender, duration, popularity, explicit, classic), artists, artist similarity and the KaraokeNerds id map (`kn_song_map`). Built by the weekly `mb-refresh` job (step 8, `karaoke_decide/catalog/`, best-effort after the app tables publish) from the fresh BigQuery tables, gated against the previous build (`catalog_gate`: min songs, max 10% song drop, max 10-point coverage drop per facet), then published to `gs://nomadkaraoke-musicbrainz-data/catalog/<run>/catalog.db.gz` behind the `catalog/latest.json` pointer (written last; 3 runs kept). Songs with neither a ListenBrainz nor a Spotify popularity signal are left out of `songs` to hold the size budget (they stay in `kn_song_map`). First prod build: 132k songs, 115 MB raw, 46 MB gzipped (budget 150 / 50 MB).
- **`availability.db`**: the search service's daily index (which songs have karaoke versions, by library), through `index_loader`.

Both are downloaded and verified (sha256) before the port opens (`backend/services/catalog_store.py`), because Cloud Run throttles CPU once it is open. `/tmp` is in-memory on Cloud Run, so the main service has 2Gi. The startup probe is `GET /api/ready` (`catalog` / `availability` booleans), every 1 s for 240 tries with CPU boost.

**Cold start (spawn to serving, target under 10 s).** In production the container runs `uvicorn backend.serve:app`: `backend/serve.py` starts the catalog + availability download in a thread (`CatalogStore.start_load`) and only then imports `backend.main`, so the download overlaps the app's import and the lifespan just waits for what is left (`load_at_startup`). Tests and the local stack import `backend.main:app`, whose lifespan does the load itself. The rest of the budget:
- **Bytecode is compiled in the image** (`compileall --invalidation-mode unchecked-hash` in the Dockerfile). The app runs as `appuser`, who can't write `__pycache__` into site-packages, so before this every cold start recompiled every imported module.
- **Heavy client libraries are imported lazily** (`karaoke_decide/core/lazy_import.py`): Firestore (with grpc and protobuf), Cloud Tasks, `jose` (cryptography) and ytmusicapi. The lifespan preloads the ones requests need (`PRELOAD_MODULES` in `backend/main.py`) in a thread alongside the catalog load. `backend/tests/test_cold_start.py` fails if `import backend.main` pulls any of them (or BigQuery or GCS) back in.
- **The startup probe polls every second**, so a ready instance gets traffic within a second. With a 10 s period it lost up to 10 s.

Measured in the production image (local, `--cpus=1`): uvicorn + app import went from 1.45 s to 0.50 s, and container start to `/api/ready` 200 (no catalog) from 1.6 s to 0.85 s. In prod (revision 00181, before this change) it was 9.7 s to uvicorn, then the 5.2 s catalog load, then up to 10 s waiting for the probe: about 20 s in all. A missing or unusable catalog never blocks startup: v2 answers 503 `warming_up` (`Retry-After: 30`) while everything else serves, and the store retries on demand every 5 minutes.

- **Ops:** `mb-refresh catalog [--no-publish] [--workdir DIR]` builds only the catalog. `--no-publish --workdir DIR` runs the real BigQuery build locally and leaves `catalog.db` there (nothing uploaded) for size checks. The first prod build (or an ad-hoc rebuild) can be started with the manual `Run catalog build` workflow (`.github/workflows/run-catalog-build.yml`) or `gcloud run jobs execute mb-refresh --region us-central1 --args=catalog --wait`. The `catalog` subcommand runs the vocal-range step first unless given `--skip-vocal-range` (the workflow's `skip_vocal_range` input passes it).
- **Infra:** the main service runtime SA already has project-level `storage.admin`, so no extra bucket IAM is declared. A `decide-api-health-uptime` check pings `/api/health` every 60 s through the Worker (idle CPU is released, so keeping an instance warm costs about nothing).

## Vocal range (Phase 3c)

`mb-refresh` runs a `vocal_range` step just before the catalog build (`karaoke_decide/etl/vocal_range_refresh.py`, skip with `--skip-vocal-range`, alone via `mb-refresh vocal-range`): UltraStar notes (archive.org dump cached in GCS), measured pyin CSVs, Gemini estimates and Wikidata voice types are merged (`karaoke_decide/vocal_range/merge.py`) into BigQuery `song_vocal_range_sources`, `song_vocal_range`, `artist_vocal_range` (staged then copied to prod) plus TSVs and a `latest.json` pointer in GCS. `catalog.db` (schema 2) and `offline.db` (additive) carry `vr_*` columns; `song-index-build` reads the per-run TSVs named by the pointer and degrades to NULL; the catalog build reads the BigQuery tables. The on-demand Cloud Run job `vocal-range-gen` (`Dockerfile.vocal-analysis`, own SA, optional poetry group `vocal-analysis`) measures gen's vocals stems. Runbook: [VOCAL-RANGE.md](VOCAL-RANGE.md).

## Voice pool (Phase 3d)

The voice session plays from a shared pool of about 1,000 songs with per-song vocal markers. Four
Cloud Run jobs build it; the API reads only the published `voice.db`. Runbook:
[VOICE-POOL.md](VOICE-POOL.md).

```
voice-pool-select (on demand, us-central1)
  catalog.db + availability.db + BigQuery Spotify ids, YouTube oEmbed probe
  → Firestore voice_pool_fetch (queued), BigQuery voice_pool_songs, voice/selection/<run>/coverage.json
        │
        ▼
voice-pool-fetch (hourly :07 UTC, scheduler voice-pool-fetch-hourly, created paused)
  flacfetch POST /download-by-id (priority batch) under its Spotify budget
  (12 h of audio per 24 h for batch, GET /spotify/budget)
  → gs://karaoke-gen-storage-nomadkaraoke/voice-pool/audio/<pool_id>/original.flac (fetched)
        │  ≥ 25 fetched or the oldest > 6 h: manifest voice-pool/manifests/<run>.jsonl
        ▼
voice-pool-separate-job (karaoke-gen, us-east4, 1 x L4, ≤ 4 tasks x 20 songs)
  → voice-pool/stems/<pool_id>/vocals.flac + <run>.results/part-<task>.jsonl
        │  collected by the next fetch run (separated)
        ▼
voice-pool-analyse (started by fetch, karaoke-decide-vocal image, 1 task)
  pyin + Spotify sections/key (BigQuery) + UltraStar density → markers (analysed)
  → BigQuery song_voice_analysis, vocal-range/pool_measured/<run>/part-*.csv
        │  task 0, after the shard
        ▼
gs://nomadkaraoke-musicbrainz-data/voice/<run>/voice.db.gz + voice/latest.json (written last)
  → API voice session; pool_measured → weekly vocal-range refresh (mb-refresh) → song_vocal_range
```

All three decide jobs run as SA `voice-pool`. Its grants are scoped: objectAdmin only under
`voice-pool/` in the gen bucket and under `voice/` and `vocal-range/pool_measured/` in the data
bucket. It holds `run.jobsExecutorWithOverrides` and `run.viewer` on gen's separation job and on
`voice-pool-analyse`, so it can start them with argument overrides and see whether one is running.
Before `pulumi up`, every job degrades with a WARNING. The API reads `VOICE_DATA_BUCKET` (voice.db)
and `VOICE_RECORDINGS_BUCKET` (`nomadkaraoke-voice-recordings`: private, uniform access, no
lifecycle) and mounts `ADMIN_TOKENS` (gen's `admin-tokens` secret).

## Data Flow

### 1. User Registration & Auth

```
User → CLI/Web → POST /api/auth/magic-link → Postmark → Email
User → Click Link → POST /api/auth/verify → JWT Token → Authenticated
```

That magic-link/JWT flow is legacy (JWTs accepted until 2026-11-30). There is now one Nomad account: gen is the identity of record. Clients send gen's session token as the Bearer token; decide validates it against gen's `sessions` collection (shared Firestore project, tenant sessions rejected) and resolves the decide profile `decide_users/{sha256(email)}` on the first request via create-if-absent. The web app signs in the same way as mobile: an email code from gen (optional Google sign-in), stored as the gen session token. Web guests were removed; a leftover guest token is claimed onto the account with `POST /api/auth/claim-guest` right after sign-in and then cleared.

### 2. Music Service Connection

```
User → Connect Spotify → OAuth Flow → Tokens stored (encrypted)
System → Periodic refresh → Keep tokens valid
```

**Token cipher.** Service tokens (Spotify, Apple Music, ListenBrainz OAuth) are encrypted at rest with Fernet (`backend/services/token_cipher.py`, key `DECIDE_TOKEN_KEY` = Secret Manager `decide-token-key`) as `enc:v1:<token>`. Values without the prefix are legacy plaintext and still read. With no key, tokens stay plaintext and ListenBrainz OAuth is off. An OAuth ListenBrainz connection whose tokens no longer decrypt (key rotated) is marked `needs_reconnect`.

**ListenBrainz OAuth (Phase 3b).** `GET /api/services/listenbrainz/oauth/start` → MetaBrainz authorize (PKCE S256, state in `oauth_states` with the verifier and `return_to`) → `/oauth/callback` stores `auth: "oauth"` tokens and 302s to the app scheme or the web page. Callbacks reject a state outside the `token_urlsafe` charset before any lookup. Live when `listenbrainz_oauth_active` (`LISTENBRAINZ_OAUTH_ENABLED`, MetaBrainz client id/secret, a valid `DECIDE_TOKEN_KEY`); otherwise the app hides the button. MetaBrainz rotates refresh tokens and revokes everything on reuse, so refreshes are serialised per user by a Firestore lease (`refresh_lease_until`, 90 s): the holder refreshes (45 s timeout) in a shielded task, and the others poll read-only for up to 50 s. A waiter timeout or a transient MetaBrainz failure is retryable (`503 listenbrainz_unavailable`), not `needs_reconnect`. `POST /listenbrainz/upload-export` proxies a Spotify export zip (≤ 30 MiB) to ListenBrainz's importer and records `lb_pending_import`.

### 2b. Imports (Phase 3b)

Screenshot (Gemini Flash, AI Studio key), public Spotify playlist (embed page), Spotify/Apple privacy-export aggregates (idempotent chunks) and YouTube Music playlists all go through `ImportIngestService` (`backend/services/import_ingest_service.py`). It matches tracks against catalog.db and merges additively into the shared `user_songs` / `user_artists` rows (the same row resolution as the karaoke log), with per-source summaries and remove-data in `/api/services/summary` and `/sources/{source}/data`.

**Local stack fakes.** `scripts/local-stack.sh` sets `DECIDE_FAKE_EXTERNALS=1` (in-process fake ListenBrainz, MetaBrainz OAuth, Spotify embed, Last.fm and Gemini under `/fake`, seeded through `/api/test/fake-*`) and `DECIDE_INLINE_TASKS=1` (Cloud Tasks are POSTed to this API in the background; `delay_seconds` is ignored, no retries). `create_app` refuses both flags whenever `environment != "development"`, and refuses to start in production with the committed test-only `DECIDE_TOKEN_KEY` (checked by sha256).

### 2c. ListenBrainz poller (`lb-poll` Cloud Run Job)

Daily at 06:30 UTC, Cloud Scheduler runs the `lb-poll` job (`python -m backend.jobs.lb_poll`, same image, SA `lb-poll`, 1800 s, no retries). It waits for catalog.db (exits 1 without it), then polls every ListenBrainz connection (never-polled first, then the oldest): the first poll backfills up to 20 pages (`play_mode=max`), later polls add new listens since the cursor (`add`). It shares a 25 req/min rate gate, marks `lb_stale` after 30 days without listens, and checks a pending export import first. It stops starting users when the 1500 s budget is spent. It logs one INFO summary per run and at most one ERROR, as JSON lines with `severity` (`configure_job_logging`, the lb-refresh/mb-refresh format) so Cloud Logging and the error monitor classify them. Local/e2e: `POST /api/test/listenbrainz/poll`.

### 3. Listening History Sync

```
POST /api/services/sync
    │
    ├─→ Spotify: Get saved tracks, top tracks, recent plays
    │       │
    │       └─→ Normalize (artist, title)
    │               │
    │               └─→ Match against karaoke_songs catalog
    │                       │
    │                       └─→ Create/update user_songs records
    │
    ├─→ Last.fm: Get top tracks, loved tracks, recent scrobbles
    │       │
    │       └─→ (same flow as Spotify)
    │
    └─→ Apple Music: recent plays + library songs (/v1/me, Music User Token from MusicKit JS)
            │
            └─→ (same flow as Spotify; artists ranked by appearances, source "apple_music")
```

**How a sync runs (resume and fencing).** `POST /api/services/sync` writes a `sync_jobs` doc (`pending`) and enqueues a Cloud Task to `/internal/sync/process`.
- **Claim.** Each delivery claims the job in one Firestore transaction (`_claim_job` in `backend/api/routes/internal.py`). A claim sets `in_progress`, a fresh `updated_at` and a new `run_id`. If another run's heartbeat is fresh, the delivery gets a 503 (`Retry-After: 60`), so Cloud Tasks retries later instead of racing it. Claiming a stalled job counts as a resume. At `MAX_SYNC_RESUMES` (5) the job is failed instead.
- **Heartbeat and fencing.** The running delivery heartbeats `updated_at` every 60 s. Every write (heartbeat, progress, result, failure) is a transaction that checks the job's `run_id` still matches. When a newer run has taken over, the progress callback raises `RunSupersededError` (`backend/models/sync_job.py`). Every per-service wrapper in `SyncService` re-raises it ahead of its broad error handler, so a superseded run stops at once, writes no per-service status, and answers 503.
- **Resume.** Last.fm resumes from its scrobble checkpoint (`oldest_scrobble_timestamp`). A run that fails puts the job back to `pending` and answers 500, so Cloud Tasks retries it.
- **Stalled.** An `in_progress` job is stalled after 300 s with no heartbeat. A `pending` job is stalled after 900 s, longer than the queue's 600 s max backoff, so a scheduled retry is never reported as lost. `GET /sync/status` shows `stalled`. `POST /sync` re-enqueues the stalled job instead of starting a second one.
- **Queue** (`music-sync-queue`, `infrastructure/__main__.py`): `max_attempts` 10, backoff 60 s → 600 s, `max_doublings` 3. These retries span the 300 s stale window, and the 5-resume cap can still be reached.

**Background work stays off the event loop.** Sync and imports run on the API service. Their blocking steps run on a bounded `ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest")` through `run_ingest` (`backend/services/ingest_executor.py`). Those steps are `TrackMatcher.batch_match_tracks` (normalising, catalog.db or the BigQuery fallback), the Last.fm artist-metadata BigQuery lookup, export-chunk aggregation, and `ImportIngestService`'s resolve, group and doc building. They never take threads from the default executor, which user requests' `asyncio.to_thread` calls (catalog search, recs) share. That executor has `min(32, cpus + 4)` workers, which is 5 on 1 vCPU. Startup logs `os.cpu_count()` and both pool sizes. `backend/tests/test_event_loop_responsiveness.py` checks that `/api/health` stays fast during a 5,000-track match and during a 20,000-track export chunk.

### 4. Song Discovery

```
GET /api/my/songs?sort=play_count
    │
    └─→ Query user_songs WHERE user_id = X
            │
            └─→ Join with karaoke_songs for full details
                    │
                    └─→ Return sorted, paginated results
```

## Collections & Indexes

### Firestore Collections

> **Note:** karaoke-decide and karaoke-gen share the same Firestore database in the `nomadkaraoke` project.
> Each app uses separate collections: `decide_users` for karaoke-decide, `gen_users` for karaoke-gen.

| Collection | Primary Key | Description |
|------------|-------------|-------------|
| `decide_users` | email hash / guest_id | User accounts (karaoke-decide) |
| `lastfm_users` | lastfm username | Last.fm users for collaborative filtering (9.9K users, MBID-first) |
| `music_services` | user_id + service_type | Connected music accounts |
| `karaoke_songs` | normalized artist-title | Song catalog (shared) |
| `user_songs` | user_id + song_id | User-song relationships |
| `user_artists` | user_id + artist_name | User-artist relationships |
| `playlists` | auto-generated, or a client `pl_…` id | User setlists: ordered `items`, `rev`, `user_id` |
| `sync_jobs` | auto-generated | Music sync job tracking |
| `sung_records` | auto-generated | Singing history |

### Composite Indexes Needed

```
user_songs:
  - (user_id ASC, play_count DESC)
  - (user_id ASC, last_played_at DESC)
  - (user_id ASC, times_sung DESC)

playlists:
  - (user_id ASC, updated_at DESC)

sung_records:
  - (user_id ASC, sung_at DESC)
```

## Personal data

Voice recordings are the one class of user audio decide stores. They are recorded only with the
session's consent (`setup.record = true` at upload time), kept private, and never expire.

- **Storage**: objects `voice-recordings/<user_id>/<session_id>/<n>.m4a|.wav` in the private
  `VOICE_RECORDINGS_BUCKET` (`nomadkaraoke-voice-recordings`, soft delete off, so a delete is real
  erasure); Firestore `voice_recordings/{session_id}:{n}` (`gcs_path` = the object name) and
  `voice_attempts.recording_id`. `VOICE_RECORDINGS_LOCAL_DIR` swaps the bucket for a directory (local stack, tests).
- **User deletion**: `DELETE /api/voice/recordings` (open to every signed-in user, even with the voice flag off)
  calls `VoiceRecordingService.delete_all_for_user(user_id)`: writes `voice_recording_purges/{user_id}`, sets
  `setup.record = false` on all the user's `voice_sessions` (withdrawing consent, so an upload the app queued
  before the delete gets `403 recording_not_consented`; the user can re-consent with PATCH), lists and deletes
  every object under the user's prefix, deletes every `voice_recordings` doc, clears `recording_id` on the
  user's attempts, returns the number erased, and is idempotent (a second call returns 0). An upload in flight
  that finds a newer purge marker undoes its own object, doc and link and returns 409 (an undo that fails is
  logged as a WARNING and still returns 409; the next delete-all sweeps what is left).
- **Account deletion contract (Phase 6)**: `DELETE /api/users/me` MUST, before removing the user, and failing
  the request (so it can be retried) if any step raises:
  1. call `VoiceRecordingService.delete_all_for_user(user.id)` (objects, `voice_recordings` docs, attempt links);
  2. delete the user's `voice_sessions`, `voice_attempts`, `voice_labels` and their
     `voice_recording_purges/{user_id}` marker;
  3. undo what sessions wrote to `user_songs`: delete rows whose only source is `voice_session`; on any other
     row strip the `session_*` fields and remove `"voice_session"` from `sources` (and from `source`, replaced
     by the first remaining source).
  4. delete the user's `playlists` documents (setlists, per `user_id`), and the user's `karaoke_log`,
     `user_songs`, `user_karaoke_unresolved` rows. Playlists are user data for this registry;
     `POST /api/test/profile/reset` already clears them (now including `playlists` and `karaoke_log`).

  `erase_voice_data(fs, bucket, user_id)` in `backend/services/user_deletion.py` does steps 1-3; account
  deletion and the test API's `POST /api/test/voice/reset` both call it. `delete_all_user_data` does step 4
  through the registry below. Any new store of user audio or voice
  data must be added there.
- **User-data registry (Phase 6)**: `backend/services/user_deletion.py` holds `USER_DATA_STORES` (every per-user
  collection and how a user's docs are found: `user_id_field`, `doc_id_user_id`, `doc_id_email_hash`,
  `email_field`) and `NOT_USER_DATA` (global collections, or ones keyed by something that isn't the user, each
  with its reason). `delete_all_user_data(fs, bucket, email)` looks the profile up at
  `decide_users/{sha256(lowercase email)}` without creating it, runs `erase_voice_data`, deletes the user's docs
  in every registry store, and deletes `decide_users` last, so a retry after a part-way failure still finds the
  user; it returns counts per collection (`{}` for an unknown email) and is idempotent. **Rule: every Firestore
  collection decide's code names must be in one of the two.** `backend/tests/test_user_data_registry_guard.py`
  parses `backend/` and `karaoke_decide/` (collection constants, FirestoreService calls, `collection=` keywords,
  transaction calls) and fails on any collection in neither. gen's `sessions` and `gen_users` are not decide's:
  gen deletes them itself, sessions last. decide keeps no import payloads in GCS (exports are parsed in the
  request); voice recordings are its only per-user objects.

## Security Considerations

1. **OAuth Tokens**: Encrypted at rest in Firestore, refreshed automatically
2. **JWT Tokens**: Short-lived (1 week), signed with secret from Secret Manager
3. **Magic Links**: Single-use, expire in 24 hours
4. **API Rate Limiting**: 100 requests/minute per user
5. **CORS**: Restricted to known domains

## BigQuery Data

### karaokenerds_raw (275,809 rows)
Karaoke song catalog from KaraokeNerds.com.

| Column | Type | Description |
|--------|------|-------------|
| Id | STRING | Unique song ID |
| Artist | STRING | Artist name |
| Title | STRING | Song title |
| Brands | STRING | Comma-separated karaoke brands |
| brand_count | INT | Number of brands (popularity signal) |

### spotify_tracks (256,039,007 rows)
Spotify track metadata from Anna's Archive dump (original ETL).

| Column | Type | Description |
|--------|------|-------------|
| spotify_id | STRING | Spotify track ID |
| title | STRING | Track title |
| artist_name | STRING | Primary artist |
| isrc | STRING | International Standard Recording Code |
| popularity | INT | Spotify popularity (0-100) |
| duration_ms | INT | Track duration |
| explicit | BOOL | Explicit content flag |

### Spotify Complete Metadata (Complete ETL - 2026-01)
Additional tables from comprehensive Spotify metadata ETL:

| Table | Rows | Description |
|-------|------|-------------|
| `spotify_artists` | ~500K | Artist metadata (id, name, followers, popularity) |
| `spotify_artist_genres` | ~2-3M | Artist-genre associations for filtering |
| `spotify_albums` | ~50M | Album metadata with release dates |
| `spotify_tracks_full` | ~256M | Full track metadata with album references |
| `spotify_track_artists` | ~300M | Track-artist junction (multi-artist support) |
| `spotify_audio_features` | ~200M | Audio features (danceability, energy, tempo, etc.) |

**ETL Script:** `scripts/spotify_complete_etl.py`
**Raw Data Archive:** `gs://nomadkaraoke-raw-archives/spotify-metadata-2025-07/` (Archive storage class)

## Deployment

- **API**: Cloud Run (auto-scaling, 0-10 instances)
- **Jobs**: `mb-refresh` (weekly), `lb-refresh` (twice weekly), `lb-poll` (daily ListenBrainz poller), `song-index-build` (daily)
- **Search**: Cloud Run `karaoke-decide-search` (0-3 instances) + daily `song-index-build` job (see [Song search](#song-search))
- **Database**: BigQuery (song catalog), Firestore (user data)
- **Secrets**: Google Secret Manager
- **Frontend**: GitHub Pages at decide.nomadkaraoke.com
- **IaC**: Pulumi (Python)

### Cloud Run Secrets Configuration

Cloud Run requires secrets from Secret Manager to be explicitly mounted:

```bash
gcloud run deploy SERVICE_NAME \
  --set-env-vars "ENVIRONMENT=production,GOOGLE_CLOUD_PROJECT=nomadkaraoke" \
  --set-secrets "JWT_SECRET=karaoke-decide-jwt-secret:latest,..."
```

**Required secrets:**
| Secret Name | Environment Variable | Purpose |
|-------------|---------------------|---------|
| `karaoke-decide-jwt-secret` | `JWT_SECRET` | JWT token signing |
| `spotipy-client-id` | `SPOTIFY_CLIENT_ID` | Spotify OAuth |
| `spotipy-client-secret` | `SPOTIFY_CLIENT_SECRET` | Spotify OAuth |
| `lastfm-api-key` | `LASTFM_API_KEY` | Last.fm API access |
| `postmark-server-token` | `POSTMARK_SERVER_TOKEN` | Email delivery |
| `decide-gen-partner-secret` | `DECIDE_GEN_PARTNER_SECRET` | Search service only: gen catalog-resolve partner auth (secret owned by karaoke-gen's Pulumi) |
| `gemini-api-key` | `GEMINI_API_KEY` | Screenshot import (AI Studio key; the screenshot card hides without it) |
| `metabrainz-client-id` / `metabrainz-client-secret` | `METABRAINZ_CLIENT_ID` / `METABRAINZ_CLIENT_SECRET` | ListenBrainz connect via MetaBrainz OAuth (also mounted on the `lb-poll` job) |
| `decide-token-key` | `DECIDE_TOKEN_KEY` | Fernet key for service tokens at rest (also on `lb-poll`) |
| `decide-kjbox-secret` | `DECIDE_KJBOX_SECRET` | Main API: kjbox's karaoke-log partner auth (`X-Kjbox-Secret`). Pulumi creates the secret; its value is added by hand (`/gcp-admin`) and must exist before a deploy that mounts it. kjbox's `decide_kjbox_secret` config holds the same value. |

**IAM requirement:** Cloud Run service account needs `roles/secretmanager.secretAccessor` on each secret. Managed via Pulumi in `infrastructure/__main__.py`.
