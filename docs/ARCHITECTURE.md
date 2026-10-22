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
| `song-index-build` job | 2 vCPU, 8 GiB (the in-memory disk shares it), timeout 1 h, no retries. Runs as SA `song-index-build`: objectViewer on kn-data plus objectAdmin limited to `song-index/` by an IAM condition; objectAdmin on the public bucket; `run.developer` on the search service only; project `run.viewer` (to read revisions); actAs on `song-search`. |
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

- **Ops:** `mb-refresh catalog [--no-publish] [--workdir DIR]` builds only the catalog. `--no-publish --workdir DIR` runs the real BigQuery build locally and leaves `catalog.db` there (nothing uploaded) for size checks. The first prod build (or an ad-hoc rebuild) can be started with the manual `Run catalog build` workflow (`.github/workflows/run-catalog-build.yml`) or `gcloud run jobs execute mb-refresh --region us-central1 --args=catalog --wait`.
- **Infra:** the main service runtime SA already has project-level `storage.admin`, so no extra bucket IAM is declared. A `decide-api-health-uptime` check pings `/api/health` every 60 s through the Worker (idle CPU is released, so keeping an instance warm costs about nothing).

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
    └─→ Last.fm: Get top tracks, loved tracks, recent scrobbles
            │
            └─→ (same flow as Spotify)
```

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
| `playlists` | auto-generated | User playlists |
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

**IAM requirement:** Cloud Run service account needs `roles/secretmanager.secretAccessor` on each secret. Managed via Pulumi in `infrastructure/__main__.py`.
