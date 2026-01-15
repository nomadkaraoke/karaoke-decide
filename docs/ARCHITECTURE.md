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
│   (256M)        │ │  SendGrid        - Magic link emails            │
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

**Configuration:** Managed via Pulumi in `infrastructure/__main__.py`. See `infrastructure/cloudflare-worker/README.md` for setup.

## Data Flow

### 1. User Registration & Auth

```
User → CLI/Web → POST /api/auth/magic-link → SendGrid → Email
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
| `sendgrid-api-key` | `SENDGRID_API_KEY` | Email delivery |

**IAM requirement:** Cloud Run service account needs `roles/secretmanager.secretAccessor` on each secret. Managed via Pulumi in `infrastructure/__main__.py`.
