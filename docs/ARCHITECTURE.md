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
         └────────┬───────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Backend API                                   │
│                    (FastAPI on Cloud Run)                           │
├─────────────────────────────────────────────────────────────────────┤
│  /api/auth/*      - Magic link authentication                       │
│  /api/services/*  - Music service OAuth & sync                      │
│  /api/catalog/*   - Karaoke song catalog                            │
│  /api/my/*        - User's matched songs, history                   │
│  /api/playlists/* - Playlist management                             │
└────────┬───────────────────┬────────────────────────────────────────┘
         │                   │
         ▼                   ▼
┌─────────────────┐ ┌─────────────────────────────────────────────────┐
│   Firestore     │ │              External Services                  │
│   (Database)    │ ├─────────────────────────────────────────────────┤
├─────────────────┤ │  Spotify API     - User listening history       │
│  users          │ │  Last.fm API     - Scrobbles and loved tracks   │
│  music_services │ │  KaraokeNerds    - Karaoke song catalog         │
│  karaoke_songs  │ │  SendGrid        - Magic link emails            │
│  user_songs     │ │                                                 │
│  playlists      │ └─────────────────────────────────────────────────┘
│  sung_records   │
└─────────────────┘
```

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

| Collection | Primary Key | Description |
|------------|-------------|-------------|
| `users` | email hash | User accounts |
| `music_services` | user_id + service_type | Connected accounts |
| `karaoke_songs` | normalized artist-title | Song catalog |
| `user_songs` | user_id + song_id | User-song relationships |
| `playlists` | auto-generated | User playlists |
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
3. **Magic Links**: Single-use, expire in 15 minutes
4. **API Rate Limiting**: 100 requests/minute per user
5. **CORS**: Restricted to known domains

## Deployment

- **API**: Cloud Run (auto-scaling, 0-10 instances)
- **Database**: Firestore (serverless)
- **Secrets**: Google Secret Manager
- **Frontend**: Cloudflare Pages (static)
- **IaC**: Pulumi (Python)
