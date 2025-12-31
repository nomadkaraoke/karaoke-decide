# Nomad Karaoke Decide - Implementation Plan

> See [VISION.md](VISION.md) for product goals and [REQUIREMENTS-QA.md](REQUIREMENTS-QA.md) for the full requirements discussion.

## Tech Stack

### Backend
- **Language:** Python 3.12
- **Framework:** FastAPI
- **Database:** Firestore (NoSQL)
- **Storage:** Google Cloud Storage
- **Secrets:** Google Secret Manager
- **Runtime:** Google Cloud Run
- **Package Manager:** Poetry

### CLI
- **Language:** Python (same package as core)
- **Entry Point:** `karaoke-decide`

### Frontend
- **Framework:** Next.js 16 with TypeScript (App Router)
- **UI:** Tailwind CSS with custom neon-noir theme
- **Hosting:** GitHub Pages (decide.nomadkaraoke.com)

### Infrastructure
- **IaC:** Pulumi (Python)
- **CI/CD:** GitHub Actions
- **Containers:** Docker

## Data Sources

### 1. Spotify Metadata Archive (Offline, ETL Required)

**Location:** `/Volumes/AndrewMacSD/spotify-metadata-dump/annas_archive_spotify_2025_07_metadata/`

**Files:**
- `spotify_clean.sqlite3.zst` (36GB compressed) - Core track/artist/album metadata
- `spotify_clean_audio_features.sqlite3.zst` (17GB compressed) - BPM, key, energy, etc.

**Data Available:**
- Track: name, duration_ms, popularity (0-100), explicit, ISRC, preview_url
- Artist: name, follower_count, popularity, genres
- Album: name, type, release_date, label
- Audio Features: BPM, key, danceability, energy, valence, acousticness

**ETL Task:** Extract relevant data, load into Firestore or BigQuery for querying.

### 2. KaraokeNerds Catalog (Daily Sync)

**Location:** `gs://projectbread-karaokay.appspot.com/karaokenerds-data/full/full-data-latest.json.gz`

**Data Available:**
- Artist, title, brands that have covered it
- Brand count as primitive popularity signal

**Access:** Requires projectbread-karaokay GCP credentials.

### 3. User Listening History (Real-Time)

**Spotify API:**
- OAuth2 for user authorization
- Saved tracks, top tracks, recently played
- Playlists and their tracks

**Last.fm API:**
- API key + user token
- Scrobbles with play counts
- Loved tracks

## Data Models

### Song (Combined Catalog)
```python
class Song:
    id: str                      # Normalized: slugify(artist-title)
    artist: str
    title: str

    # Popularity signals
    spotify_popularity: int | None  # 0-100
    karaoke_brand_count: int        # How many brands have this

    # Audio features (from Spotify dump)
    duration_ms: int | None
    bpm: float | None
    key: int | None               # Pitch class (0-11)
    mode: int | None              # 0=minor, 1=major
    energy: float | None          # 0-1
    danceability: float | None    # 0-1
    valence: float | None         # 0-1 (positivity)

    # Metadata
    genres: list[str]
    decade: str | None            # "1980s", "1990s", etc.
    explicit: bool

    # Karaoke availability
    has_karaoke: bool
    karaoke_youtube_url: str | None

    # Future: vocal analysis
    vocal_range_low: str | None   # e.g., "C3"
    vocal_range_high: str | None  # e.g., "G5"
```

### User
```python
class User:
    id: str
    email: str
    display_name: str | None
    created_at: datetime

    # Quiz responses (for data-light users)
    quiz_songs_known: list[str]   # Song IDs they recognized
    quiz_decade_pref: str | None
    quiz_energy_pref: str | None  # "chill", "medium", "high"

    # Vocal range (optional)
    vocal_range_low: str | None
    vocal_range_high: str | None
    voice_type: str | None        # "baritone", "soprano", etc.

    # Stats
    last_sync_at: datetime | None
```

### MusicService (Connected Accounts)
```python
class MusicService:
    id: str
    user_id: str
    service_type: Literal["spotify", "lastfm"]
    service_user_id: str

    # OAuth tokens
    access_token: str | None
    refresh_token: str | None
    token_expires_at: datetime | None

    # Sync state
    tracks_synced: int
    last_sync_at: datetime | None
```

### UserSong (User's Relationship to Songs)
```python
class UserSong:
    id: str                       # user_id:song_id
    user_id: str
    song_id: str

    # From listening history
    play_count: int
    last_played_at: datetime | None
    is_saved: bool
    source: Literal["spotify", "lastfm", "quiz"]

    # Denormalized
    artist: str
    title: str
```

### Playlist
```python
class Playlist:
    id: str
    user_id: str
    name: str
    song_ids: list[str]
    created_at: datetime
    updated_at: datetime
```

## API Endpoints

### Auth
```
POST /api/auth/magic-link     - Request magic link email
POST /api/auth/verify         - Verify token, get JWT
GET  /api/auth/me             - Get current user
POST /api/auth/logout         - Invalidate session
```

### Music Services
```
GET  /api/services            - List connected services
POST /api/services/spotify/connect    - Start OAuth
GET  /api/services/spotify/callback   - OAuth callback
POST /api/services/lastfm/connect     - Connect Last.fm
POST /api/services/sync       - Trigger history sync
GET  /api/services/sync/status
```

### Onboarding Quiz
```
GET  /api/quiz/songs          - Get quiz song options (popular karaoke)
POST /api/quiz/submit         - Submit quiz responses
GET  /api/quiz/status         - Get quiz completion status
```

### Song Discovery
```
GET  /api/songs               - Search/browse catalog
GET  /api/songs/:id           - Get song details
GET  /api/songs/recommend     - Get recommendations for user
```

Query params for /api/songs:
- `q` - Text search
- `decade` - Filter by decade
- `genre` - Filter by genre
- `energy` - min/max energy
- `bpm` - min/max BPM
- `popularity` - min popularity
- `in_my_library` - Only songs user knows
- `has_karaoke` - Only existing karaoke versions
- `sort` - play_count, popularity, artist, title

### My Songs
```
GET  /api/my/songs            - Songs from my listening history
GET  /api/my/recommendations  - Personalized song recommendations
```

### Playlists
```
GET    /api/playlists
POST   /api/playlists
GET    /api/playlists/:id
PUT    /api/playlists/:id
DELETE /api/playlists/:id
POST   /api/playlists/:id/songs
DELETE /api/playlists/:id/songs/:songId
```

### Karaoke Links
```
GET /api/songs/:id/karaoke    - Get karaoke video URL or generator link
```

Returns either:
- `{ "type": "youtube", "url": "https://youtube.com/..." }`
- `{ "type": "generate", "url": "https://gen.nomadkaraoke.com/?artist=...&title=..." }`

## CLI Commands

```bash
# Auth
karaoke-decide auth login
karaoke-decide auth status
karaoke-decide auth logout

# Services
karaoke-decide services list
karaoke-decide services connect spotify
karaoke-decide services connect lastfm
karaoke-decide services sync

# Quiz (for data-light testing)
karaoke-decide quiz start
karaoke-decide quiz status

# Songs
karaoke-decide songs search "bohemian"
karaoke-decide songs recommend
karaoke-decide songs recommend --decade 90s --energy high
karaoke-decide songs mine

# Playlists
karaoke-decide playlist list
karaoke-decide playlist create "Friday Night"
karaoke-decide playlist add <playlist_id> <song_id>
karaoke-decide playlist show <playlist_id>
```

## Development Phases

### Phase 1: Data Foundation ✅ COMPLETE
1. [x] ETL Spotify metadata dump → extract to usable format (256M tracks to BigQuery)
2. [x] Download and parse KaraokeNerds catalog (275K songs to BigQuery)
3. [x] Design combined song schema
4. [x] Load full dataset to BigQuery
5. [x] Basic song search API with BigQuery backend

### Phase 2: Auth & User Management ✅ COMPLETE
1. [x] Magic link email flow (SendGrid)
2. [x] JWT token management
3. [x] User profile storage
4. [x] Session management

### Phase 3: Music Service Integration ✅ COMPLETE
1. [x] Spotify OAuth flow
2. [x] Spotify listening history fetch
3. [x] Last.fm API integration
4. [x] Background sync job
5. [x] UserSong matching to catalog

### Phase 4: Quiz & Recommendations ✅ COMPLETE
1. [x] Quiz song selection (popular karaoke)
2. [x] Quiz submission and storage
3. [x] Recommendation algorithm v1
4. [x] Filter/sort implementation

### Phase 5: Frontend Auth & Discovery ✅ COMPLETE
1. [x] Auth flow (magic link, JWT storage, protected routes)
2. [x] My Songs page with listening history
3. [x] Recommendations page with personalized suggestions
4. [x] Quiz onboarding UI
5. [x] Services connection page (Spotify OAuth, Last.fm)

### Phase 6: Playlists, Karaoke Links & Profile ✅ COMPLETE
1. [x] Playlist CRUD (create, read, update, delete, add/remove songs)
2. [x] Karaoke link lookup (YouTube search, Generator handoff)
3. [x] User profile page with display name management

### Phase 7: CLI Polish (Post-MLP)
1. [ ] All commands implemented
2. [ ] Rich terminal output
3. [ ] Interactive browse mode

### Phase 8: Infrastructure (Post-MLP)
1. [x] Pulumi GCP setup
2. [x] Cloud Run deployment
3. [x] CI/CD pipeline
4. [ ] Monitoring & logging

## Future Phases (Post-MLP)

### Vocal Range Detection
1. [ ] Research existing APIs (Cyanite, etc.)
2. [ ] Web Audio API pitch detection prototype
3. [ ] Song vocal range analysis pipeline
4. [ ] Pre-compute top 1000 songs
5. [ ] User vocal range → song matching

### Performance Tracking
1. [ ] Post-song survey
2. [ ] Rating and notes storage
3. [ ] "Songs I've sung" history

### Social Features
1. [ ] Friend connections
2. [ ] Group playlists
3. [ ] Karaoke crews

### Venue Integration
1. [ ] Venue database
2. [ ] Filter by venue availability

## Testing Strategy

```bash
make test         # All tests
make test-unit    # Unit tests only
make test-backend # Backend tests
make test-e2e     # With emulators
```

### Coverage Targets
- Core package: 80%+
- Backend: 70%+
- CLI: 60%+

## Notes on Data Access

### KaraokeNerds Catalog
Currently requires `projectbread-karaokay` GCP credentials. Options:
1. Copy data to `nomadkaraoke` project
2. Set up cross-project access
3. Download manually and upload to new location

### Spotify Dump
Large files require:
1. Decompress with zstd (requires disk space)
2. Extract relevant columns to smaller format
3. Consider BigQuery for full-scale queries
4. Local SQLite subset for development

## Success Criteria

MLP is complete when:
- [x] User can sign up via magic link
- [x] User can connect Spotify OR complete quiz
- [x] User sees relevant song recommendations
- [x] User can search and filter songs
- [x] User can create playlists
- [x] Songs link to YouTube karaoke or Generator
- [x] API is deployed and stable
- [x] Web frontend is responsive and usable
- [x] 70%+ test coverage

**MLP COMPLETE as of 2025-12-30** - See [archive/2025-12-30-phase6-mlp-completion.md](archive/2025-12-30-phase6-mlp-completion.md)
