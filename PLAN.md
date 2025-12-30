# Nomad Karaoke Decide - Project Plan

## Vision

**Nomad Karaoke Decide** helps people discover and choose the perfect karaoke songs to sing. Unlike the original KaraokeHunt app which only listed songs, this product will actually deliver on the core value proposition: **helping you decide what to sing**.

**Domain:** decide.nomadkaraoke.com

## Core Value Proposition

1. **Know what you know** - Connect your music listening history (Spotify, Last.fm, Apple Music) to see songs you actually recognize
2. **Know what's available** - Comprehensive karaoke catalog from multiple sources
3. **Know what you can sing** - Vocal range detection and filtering
4. **Track what works** - Record which songs you've sung and how they went

## Tech Stack (Following karaoke-gen Patterns)

### Backend
- **Language:** Python 3.12
- **Framework:** FastAPI
- **Database:** Firestore (NoSQL document store)
- **Storage:** Google Cloud Storage (for any file uploads)
- **Secrets:** Google Secret Manager
- **Runtime:** Google Cloud Run
- **Package Manager:** Poetry

### CLI
- **Language:** Python (same package as backend core)
- **Entry Points:** `karaoke-decide` (main CLI)

### Frontend (Phase 2)
- **Framework:** Next.js with TypeScript
- **UI:** Radix UI + Tailwind CSS
- **Hosting:** Cloudflare Pages

### Infrastructure
- **IaC:** Pulumi (Python)
- **CI/CD:** GitHub Actions
- **Containers:** Docker

## Minimum Lovable Product (MLP) - Phase 1

### Core Features

#### 1. User Management
- Magic link authentication (email-based, no passwords)
- User profile with optional display name
- Store connected service credentials securely

#### 2. Music Service Integration
- **Spotify Integration** (primary)
  - OAuth2 flow for user authorization
  - Fetch user's saved tracks, top tracks, recently played
  - Fetch playlists and their tracks
- **Last.fm Integration** (secondary)
  - API key auth + user token
  - Fetch listening history with play counts
  - Fetch loved tracks
- Store normalized listening data per user

#### 3. Karaoke Catalog
- Fetch and store karaoke song catalog from KaraokeNerds API
- Periodic refresh (daily/weekly cron)
- Normalized track model: artist, title, source, external_id

#### 4. Personal Song Matching
- Match user's listening history against karaoke catalog
- Rank songs by:
  - Play count (most listened = most familiar)
  - Recency (recently played = fresh in memory)
  - Saved/loved status
- Expose matched songs via API

#### 5. Song Search & Browse
- Full-text search across karaoke catalog
- Filter by: artist, title, in_my_library, play_count_min
- Sort by: relevance, play_count, artist, title

#### 6. Personal Playlists
- Create, update, delete playlists
- Add/remove songs from playlists
- Mark songs as "sung" with optional rating (1-5) and notes

### CLI Commands

```bash
# Authentication
karaoke-decide auth login              # Start magic link flow
karaoke-decide auth status             # Show current user
karaoke-decide auth logout             # Clear credentials

# Music Services
karaoke-decide services list           # Show connected services
karaoke-decide services connect spotify # OAuth flow for Spotify
karaoke-decide services connect lastfm  # Connect Last.fm
karaoke-decide services sync           # Manually trigger history sync

# Song Discovery
karaoke-decide songs search "bohemian" # Search karaoke catalog
karaoke-decide songs browse            # Interactive browse with filters
karaoke-decide songs mine              # Show my matched songs (songs I know)
karaoke-decide songs top               # Show my top karaoke picks

# Playlists
karaoke-decide playlist list           # List my playlists
karaoke-decide playlist create "Friday Night"
karaoke-decide playlist show <id>
karaoke-decide playlist add <playlist_id> <song_id>
karaoke-decide playlist remove <playlist_id> <song_id>

# Song Tracking
karaoke-decide sung <song_id> --rating 4 --notes "Crowd loved it"
karaoke-decide history                 # Show songs I've sung
```

### API Endpoints

```
# Auth
POST   /api/auth/magic-link           # Request magic link
POST   /api/auth/verify               # Verify magic link token
GET    /api/auth/me                   # Get current user
POST   /api/auth/logout               # Invalidate session

# Music Services
GET    /api/services                  # List connected services
POST   /api/services/spotify/connect  # Start Spotify OAuth
GET    /api/services/spotify/callback # Spotify OAuth callback
POST   /api/services/lastfm/connect   # Connect Last.fm
POST   /api/services/sync             # Trigger history sync
GET    /api/services/sync/status      # Get sync status

# Catalog
GET    /api/catalog/songs             # Search/browse catalog
GET    /api/catalog/songs/:id         # Get song details
GET    /api/catalog/stats             # Catalog statistics

# My Songs (personalized)
GET    /api/my/songs                  # My matched songs with listen data
GET    /api/my/songs/top              # Top recommendations
GET    /api/my/history                # Songs I've sung

# Playlists
GET    /api/playlists                 # List my playlists
POST   /api/playlists                 # Create playlist
GET    /api/playlists/:id             # Get playlist
PUT    /api/playlists/:id             # Update playlist
DELETE /api/playlists/:id             # Delete playlist
POST   /api/playlists/:id/songs       # Add song to playlist
DELETE /api/playlists/:id/songs/:songId # Remove song

# Song Tracking
POST   /api/my/songs/:id/sung         # Mark song as sung with rating
```

## Data Models

### User
```python
class User:
    id: str                    # Firestore doc ID
    email: str                 # Unique, primary identifier
    display_name: Optional[str]
    created_at: datetime
    updated_at: datetime

    # Aggregated stats (denormalized for fast reads)
    total_songs_known: int     # Songs matched from listening history
    total_songs_sung: int      # Songs marked as sung
    last_sync_at: Optional[datetime]
```

### MusicService (connected accounts)
```python
class MusicService:
    id: str
    user_id: str
    service_type: Literal["spotify", "lastfm", "apple_music"]
    service_user_id: str       # User's ID on that service
    service_username: str      # Display name on that service

    # OAuth tokens (encrypted)
    access_token: Optional[str]
    refresh_token: Optional[str]
    token_expires_at: Optional[datetime]

    # Sync state
    last_sync_at: Optional[datetime]
    sync_status: Literal["idle", "syncing", "error"]
    sync_error: Optional[str]
    tracks_synced: int

    created_at: datetime
    updated_at: datetime
```

### KaraokeSong (catalog)
```python
class KaraokeSong:
    id: str                    # Normalized ID: slugify(artist-title)
    artist: str
    title: str

    # Source tracking
    sources: List[SongSource]  # Which karaoke services have this

    # Optional metadata (enriched later)
    duration_ms: Optional[int]
    genres: List[str]
    popularity: Optional[int]  # 0-100 from Spotify

    # Flags
    is_popular_karaoke: bool   # On "top karaoke songs" lists

    created_at: datetime
    updated_at: datetime

class SongSource:
    source: Literal["karaokenerds", "openkj", "karafun"]
    external_id: str
    url: Optional[str]
```

### UserSong (user's relationship to a song)
```python
class UserSong:
    id: str                    # user_id:song_id
    user_id: str
    song_id: str               # References KaraokeSong.id

    # From listening history
    play_count: int            # Total plays across services
    last_played_at: Optional[datetime]
    is_saved: bool             # In user's library/favorites

    # From user tracking
    times_sung: int
    last_sung_at: Optional[datetime]
    average_rating: Optional[float]  # 1-5
    notes: Optional[str]

    # Denormalized for queries
    artist: str
    title: str

    updated_at: datetime
```

### Playlist
```python
class Playlist:
    id: str
    user_id: str
    name: str
    description: Optional[str]

    song_ids: List[str]        # Ordered list of KaraokeSong IDs
    song_count: int            # Denormalized

    created_at: datetime
    updated_at: datetime
```

### SungRecord (individual singing event)
```python
class SungRecord:
    id: str
    user_id: str
    song_id: str

    sung_at: datetime
    rating: Optional[int]      # 1-5
    notes: Optional[str]

    # Optional context
    venue: Optional[str]
    playlist_id: Optional[str]
```

## Infrastructure (Pulumi)

### GCP Resources

```python
# Firestore
- Database: karaoke-decide-db (FIRESTORE_NATIVE, us-central1)
- Collections: users, music_services, karaoke_songs, user_songs, playlists, sung_records
- Composite indexes for common queries

# Cloud Storage
- Bucket: karaoke-decide-storage-{project}
- Used for: catalog snapshots, export data

# Cloud Run
- Service: karaoke-decide-api
- Memory: 512MB (lightweight API)
- CPU: 1
- Min instances: 0, Max: 10
- Concurrency: 80

# Secret Manager
- spotify-client-id
- spotify-client-secret
- lastfm-api-key
- sendgrid-api-key (for magic links)

# Cloud Scheduler (cron)
- catalog-sync: Daily at 3am UTC
- token-refresh: Hourly (refresh expiring OAuth tokens)

# Service Account
- karaoke-decide-backend: Firestore, Storage, Secrets, Scheduler
```

## Project Structure

```
karaoke-decide/
├── karaoke_decide/              # Core Python package
│   ├── __init__.py
│   ├── cli/                     # CLI implementation
│   │   ├── __init__.py
│   │   ├── main.py              # Entry point
│   │   ├── auth.py              # Auth commands
│   │   ├── services.py          # Service connection commands
│   │   ├── songs.py             # Song discovery commands
│   │   └── playlists.py         # Playlist commands
│   ├── core/                    # Shared business logic
│   │   ├── __init__.py
│   │   ├── models.py            # Pydantic models
│   │   ├── config.py            # Settings management
│   │   └── exceptions.py        # Custom exceptions
│   ├── services/                # External service integrations
│   │   ├── __init__.py
│   │   ├── spotify.py           # Spotify API client
│   │   ├── lastfm.py            # Last.fm API client
│   │   └── karaokenerds.py      # KaraokeNerds catalog fetcher
│   └── utils/                   # Utilities
│       ├── __init__.py
│       └── text.py              # String normalization, etc.
│
├── backend/                     # FastAPI backend
│   ├── __init__.py
│   ├── main.py                  # FastAPI app
│   ├── config.py                # Backend-specific settings
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── services.py
│   │   │   ├── catalog.py
│   │   │   ├── my_songs.py
│   │   │   └── playlists.py
│   │   └── deps.py              # Dependency injection
│   ├── models/                  # Request/response models
│   │   └── ...
│   ├── services/                # Backend services
│   │   ├── __init__.py
│   │   ├── firestore_service.py
│   │   ├── auth_service.py
│   │   ├── user_service.py
│   │   ├── catalog_service.py
│   │   ├── sync_service.py
│   │   └── email_service.py
│   ├── middleware/
│   │   └── audit_logging.py
│   └── tests/
│       └── ...
│
├── frontend/                    # Next.js frontend (Phase 2)
│   └── ...
│
├── infrastructure/              # Pulumi IaC
│   ├── __main__.py
│   ├── Pulumi.yaml
│   ├── Pulumi.dev.yaml
│   └── Pulumi.prod.yaml
│
├── tests/                       # Package tests
│   ├── unit/
│   └── integration/
│
├── scripts/                     # Dev scripts
│   ├── start-emulators.sh
│   ├── stop-emulators.sh
│   └── seed-catalog.py
│
├── docs/                        # Documentation
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md
│   ├── API.md
│   └── archive/
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── .claude/
│   └── commands/
│       └── ...
│
├── CLAUDE.md                    # AI agent guidelines
├── CONTEXT.md                   # Project background
├── PLAN.md                      # This file
├── Dockerfile
├── Makefile
├── pyproject.toml
├── poetry.lock
├── .gitignore
├── .env.example
└── README.md
```

## Development Phases

### Phase 1: Foundation (Current)
1. Project setup (repo, structure, deps)
2. Core models and configuration
3. Firestore service layer
4. Auth system (magic links)
5. Basic CLI structure

### Phase 2: Catalog & Matching
1. KaraokeNerds catalog fetcher
2. Spotify OAuth integration
3. Last.fm integration
4. Listening history sync
5. Song matching algorithm

### Phase 3: API & CLI Complete
1. All API endpoints implemented
2. Full CLI functionality
3. Playlist management
4. Song tracking (sung records)
5. Comprehensive tests

### Phase 4: Infrastructure & Deploy
1. Pulumi infrastructure
2. Cloud Run deployment
3. CI/CD pipeline
4. Monitoring & logging

### Phase 5: Web Frontend
1. Next.js project setup
2. Auth flow
3. Song discovery UI
4. Playlist management UI
5. Mobile-responsive design

## Testing Strategy

Following karaoke-gen patterns:

```bash
# Unit tests (fast, mocked)
make test-unit

# Backend tests with emulators
make test-e2e

# All tests
make test
```

### Coverage Targets
- Backend: 70%+
- Core package: 80%+
- CLI: 60%+ (harder to test interactive)

## Success Metrics

### MLP Launch Criteria
- [ ] User can sign up via magic link
- [ ] User can connect Spotify account
- [ ] User can see their listening history matched to karaoke songs
- [ ] User can search the full karaoke catalog
- [ ] User can create and manage playlists
- [ ] User can mark songs as sung with ratings
- [ ] CLI provides full functionality
- [ ] API is deployed and accessible
- [ ] 70%+ test coverage

### Future Metrics (Post-MLP)
- Monthly active users
- Songs matched per user
- Playlists created
- Songs marked as sung
- User retention (7-day, 30-day)
