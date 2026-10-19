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
│  /api/auth/*      - Magic link authentication              ✅ LIVE  │
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

The same Worker also routes `/api/search` and `/api/search/*` to the search service (`SEARCH_BACKEND_URL`), keeping the client's `CF-Connecting-IP` for its per-IP rate limit. It also serves `decide.nomadkaraoke.com/offline-index/<run-token>/offline.db.gz` as an edge-cached (7-day) passthrough to the public `nomadkaraoke-song-index-public` bucket. Only that exact path shape is fetched; anything else under `/offline-index/` is a 404. See [Song search](#song-search).

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
  2. Run the job once with `--no-deploy`. It publishes and writes `latest.json` without a rollout.
  3. Run `pulumi config rm searchServiceEnabled && pulumi up`.

  Until the service exists, CI skips its search deploy and job-image steps with a warning.

## Data Flow

### 1. User Registration & Auth

```
User → CLI/Web → POST /api/auth/magic-link → Postmark → Email
User → Click Link → POST /api/auth/verify → JWT Token → Authenticated
```

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
