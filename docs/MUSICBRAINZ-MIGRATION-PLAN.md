# MusicBrainz-First Architecture Migration Plan

## Executive Summary

Migrate from Spotify IDs as primary identifiers to **MusicBrainz IDs (MBIDs)** as the canonical source of truth. Spotify and Last.fm data become enrichment layers rather than primary sources.

**Why:**
- MusicBrainz has periodic data dumps + live feed → stays current
- Spotify data is a July 2025 snapshot → will become stale
- MusicBrainz is open data with community curation
- Already using MBID-based ListenBrainz for recommendations
- MLHD+ import (in progress by another agent) uses MBIDs

## Current vs Target Architecture

### Current: Spotify-Centric
```
spotify_artist_id (PK) → artist_name, genres, popularity
spotify_track_id (PK)  → title, audio_features, popularity
user_data              → references spotify_artist_id
```

### Target: MusicBrainz-Centric
```
artist_mbid (PK) → name, disambiguation, type, area, tags
                 ↳ spotify_artist_id (enrichment, nullable)
                 ↳ popularity, genres (from Spotify if available)

recording_mbid (PK) → title, length, artist_credits
                    ↳ spotify_track_id (enrichment, nullable)
                    ↳ audio_features (from Spotify if available)

user_data → references artist_mbid (with spotify_id as fallback display)
```

## Data Layer Changes

### New BigQuery Tables (MusicBrainz-Primary)

```sql
-- Primary artist table (from MusicBrainz dumps)
CREATE TABLE karaoke_decide.mb_artists (
    artist_mbid STRING NOT NULL,           -- Primary key
    name STRING NOT NULL,
    sort_name STRING,
    disambiguation STRING,                  -- "UK rock band" vs "70s funk"
    artist_type STRING,                     -- Person, Group, Orchestra, etc.
    begin_date DATE,
    end_date DATE,
    area_name STRING,                       -- Country/city
    gender STRING,                          -- For solo artists
    updated_at TIMESTAMP,                   -- From MB dump timestamp

    -- Enrichment from Spotify (nullable)
    spotify_artist_id STRING,
    spotify_popularity INT64,
    spotify_followers INT64,
    spotify_genres ARRAY<STRING>,

    -- Enrichment from Last.fm (nullable)
    lastfm_listeners INT64,
    lastfm_playcount INT64,
);

-- Primary recording table (from MusicBrainz dumps)
CREATE TABLE karaoke_decide.mb_recordings (
    recording_mbid STRING NOT NULL,         -- Primary key
    title STRING NOT NULL,
    length_ms INT64,
    artist_credit STRING,                   -- Display string "Artist feat. Other"
    first_release_date DATE,
    updated_at TIMESTAMP,

    -- Enrichment from Spotify (nullable)
    spotify_track_id STRING,
    spotify_popularity INT64,
    spotify_audio_features STRUCT<
        danceability FLOAT64,
        energy FLOAT64,
        valence FLOAT64,
        tempo FLOAT64,
        acousticness FLOAT64,
        instrumentalness FLOAT64
    >,

    -- Karaoke availability
    has_karaoke_version BOOLEAN,
    karaoke_source STRING,
);

-- Artist-Recording relationship (from MusicBrainz)
CREATE TABLE karaoke_decide.mb_artist_recordings (
    artist_mbid STRING NOT NULL,
    recording_mbid STRING NOT NULL,
    credit_type STRING,                     -- main, featured, remixer, etc.
);

-- Artist tags/genres (from MusicBrainz community tags)
CREATE TABLE karaoke_decide.mb_artist_tags (
    artist_mbid STRING NOT NULL,
    tag_name STRING NOT NULL,
    vote_count INT64,                       -- Community votes
);

-- Mapping table for lookups
CREATE TABLE karaoke_decide.id_mappings (
    artist_mbid STRING,
    spotify_artist_id STRING,
    lastfm_artist_name STRING,              -- Normalized for lookup
    match_confidence FLOAT64,
    match_method STRING,                    -- exact_link, name_match, fuzzy
);
```

### Migration Path for Existing Tables

Keep existing Spotify tables but treat as enrichment source:

```sql
-- Existing tables become "source" tables
-- spotify_artists → source for enrichment
-- spotify_tracks → source for enrichment
-- spotify_audio_features → source for enrichment

-- Views for backward compatibility during migration
CREATE VIEW karaoke_decide.artists AS
SELECT
    m.artist_mbid AS id,                    -- New primary
    m.name,
    m.disambiguation,
    m.spotify_artist_id,                    -- For enrichment
    COALESCE(m.spotify_popularity, 50) AS popularity,
    COALESCE(m.spotify_genres, []) AS genres,
    m.area_name AS country,
FROM karaoke_decide.mb_artists m;
```

## API Layer Changes

### Artist Response Model

```python
# Current
class Artist(BaseModel):
    spotify_id: str                         # Primary
    name: str
    genres: list[str]
    popularity: int

# New
class Artist(BaseModel):
    mbid: str                               # Primary
    name: str
    disambiguation: str | None              # "UK rock band"
    artist_type: str | None                 # "Group", "Person"

    # Enrichment (nullable)
    spotify_id: str | None
    spotify_popularity: int | None
    spotify_genres: list[str] | None

    # Community data
    tags: list[str] | None                  # From MusicBrainz
```

### Endpoint Changes

```python
# Current
@router.get("/api/catalog/artists/{spotify_id}")

# New - support both, prefer MBID
@router.get("/api/catalog/artists/{id}")
async def get_artist(id: str):
    # Detect ID type
    if is_mbid(id):  # UUID format
        return await get_artist_by_mbid(id)
    elif is_spotify_id(id):  # Base62 format
        return await get_artist_by_spotify_id(id)  # Lookup via mapping
    else:
        raise HTTPException(400, "Invalid artist ID format")
```

### Search/Autocomplete

```python
# Current: searches spotify_artists by name
# New: searches mb_artists, returns MBID as primary

@router.get("/api/catalog/artists")
async def search_artists(q: str, limit: int = 20):
    """Search artists by name, returns MusicBrainz-primary results."""

    results = await bigquery.query("""
        SELECT
            artist_mbid AS id,
            name,
            disambiguation,
            spotify_artist_id,
            spotify_popularity AS popularity
        FROM karaoke_decide.mb_artists
        WHERE LOWER(name) LIKE LOWER(@query)
        ORDER BY spotify_popularity DESC NULLS LAST
        LIMIT @limit
    """, {"query": f"%{q}%", "limit": limit})

    return results
```

## User Data Changes

### Firestore Collections

```python
# Current: decide_users stores spotify_artist_ids
{
    "quiz_artists_known": ["3TVXtAsR1...", "0L8ExT02..."],  # Spotify IDs
}

# New: store MBIDs as primary, keep Spotify for backward compat
{
    "quiz_artists_known_mbid": [
        "a74b1b7f-71a5-4011-9441-d0b5e4122711",
        "8538e728-ca0b-4321-b7e5-cff6565dd4c0"
    ],
    "quiz_artists_known": ["3TVXtAsR1...", "0L8ExT02..."],  # Deprecated, for migration
}
```

### Last.fm Users Collection

Already has MBIDs! Just store them properly:

```python
# lastfm_users collection
{
    "lastfm_username": "beveradb",
    "top_artists": [
        {
            "mbid": "eab76c9f-ff91-4431-b6dd-3b976c598020",  # Primary
            "name": "Infected Mushroom",
            "playcount": 4491,
            "spotify_id": "6S2tas...",                        # Enrichment
        }
    ],
    "artist_mbids": ["eab76c9f-...", ...],  # For array_contains queries
}
```

## Recommendation Logic Changes

### Current Flow
```
User selects artists (Spotify IDs)
    → Query ListenBrainz (convert to MBID first)
    → Get similar artists (MBIDs)
    → Convert back to Spotify IDs
    → Return Spotify-based results
```

### New Flow
```
User selects artists (MBIDs)
    → Query ListenBrainz directly (MBIDs)
    → Query MLHD+ co-occurrence (MBIDs)
    → Query Last.fm users (MBIDs)
    → Merge & rank (all MBID-based)
    → Enrich with Spotify data where available
    → Return MBID-primary results
```

## Frontend Changes

### Artist Card Component

```typescript
// Current
interface Artist {
    spotify_id: string;  // Primary
    name: string;
}

// New
interface Artist {
    mbid: string;        // Primary
    name: string;
    disambiguation?: string;
    spotify_id?: string; // For Spotify links, images
}

// Display logic
function ArtistCard({ artist }: { artist: Artist }) {
    // Use Spotify data for rich display if available
    const spotifyUrl = artist.spotify_id
        ? `https://open.spotify.com/artist/${artist.spotify_id}`
        : null;

    // Use MBID for internal references
    const detailUrl = `/artist/${artist.mbid}`;

    return (
        <div>
            <h3>{artist.name}</h3>
            {artist.disambiguation && (
                <span className="text-muted">({artist.disambiguation})</span>
            )}
        </div>
    );
}
```

## Migration Steps

### Phase 1: Data Foundation ✅ PARTIAL (2026-01-14)
- [ ] Create `mb_artists` table from MusicBrainz dump (NOT YET - blocked on another agent)
- [ ] Create `mb_recordings` table (NOT YET)
- [ ] Create `id_mappings` table (NOT YET)
- [x] **Discover: Last.fm API already returns MBIDs** (~80-87% coverage)

### Phase 2: API Dual-Support (Not Started)
- [ ] Update API models to include both IDs
- [ ] Add MBID lookup endpoints
- [ ] Keep Spotify ID endpoints working (backward compat)
- [ ] Update search to return MBID-primary results

### Phase 3: Recommendation Migration ✅ COMPLETE (2026-01-14)
- [x] Update `_get_collaborative_suggestions()` for MBID-first
  - Now queries BOTH `decide_users` AND `lastfm_users` in parallel
  - Uses `artist_names_lower` for name-based matching (backwards compat)
  - Ready for MBID queries when mapping table exists
- [x] Update Last.fm import to store MBIDs properly
  - `scripts/lastfm_firestore_import.py` now extracts MBIDs from Last.fm API
  - Stores `artist_mbids` array for MBID-based queries
  - Maintains `artist_names_lower` for backwards compatibility
- [ ] Integrate MLHD+ data (from other agent's work)
- [x] Test recommendation quality (unit tests passing)

### Phase 4: User Data Migration (Not Started)
- [ ] Add `quiz_artists_known_mbid` field
- [ ] Migrate existing users' Spotify IDs → MBIDs
- [ ] Update quiz submission to store MBIDs
- [ ] Deprecate Spotify-only fields

### Phase 5: Frontend Update (Not Started)
- [ ] Update TypeScript interfaces
- [ ] Update artist selection components
- [ ] Add disambiguation display where helpful
- [ ] Test full flow

### Phase 6: Cleanup (Not Started)
- [ ] Remove deprecated Spotify-only code paths
- [ ] Archive old Spotify-primary tables
- [x] Update documentation
- [ ] Set up MusicBrainz dump refresh automation

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Not all artists have Spotify mapping | Some artists won't have Spotify enrichment | Accept - MB data is still useful |
| MBID format unfamiliar to users | UX confusion | Never show raw MBIDs, always show names |
| Breaking existing user data | Users lose preferences | Dual-write during migration, gradual cutover |
| Performance of MBID lookups | Slower queries | Index properly, cache aggressively |

## Success Criteria

1. **All new data** stored with MBID as primary key
2. **All recommendations** flow through MBID-based logic
3. **Spotify enrichment** available for 80%+ of popular artists
4. **No user-facing breakage** during migration
5. **MusicBrainz dump refresh** automated (weekly)

## Coordination with MLHD+ Import

The other agent working on MLHD+ import should:
1. Store all data keyed by MBID
2. Create the `mb_artists` table from MusicBrainz dumps
3. Build co-occurrence matrix with MBID pairs
4. Set up automated refresh from MusicBrainz

This migration plan assumes that work will provide the foundational MusicBrainz tables.

## Questions to Resolve

1. **Spotify fallback:** If an artist has no MBID, do we still support them via Spotify-only?
2. **Image sources:** MusicBrainz doesn't have artist images - continue using Spotify/Last.fm?
3. **Genre source:** MusicBrainz has community tags, Spotify has algorithmic genres - which to prefer?
4. **Recording vs Track:** MusicBrainz distinguishes recordings (performance) from works (composition) - how deep do we go?

## Next Steps

1. ~~**Update Last.fm import** - store MBIDs properly~~ ✅ DONE (2026-01-14)
2. ~~**Update collaborative filtering** - query lastfm_users collection~~ ✅ DONE (2026-01-14)
3. **Wait for MusicBrainz tables** - another agent to create `mb_artists` etc.
4. **Create MBID↔Spotify mapping** - for bridging quiz artists to Last.fm users
5. **Plan API versioning** - v1 (Spotify) vs v2 (MBID-first)?
6. **Enable MBID-based queries** - switch from `artist_names_lower` to `artist_mbids`

## Implementation Notes (2026-01-14)

### Key Discovery
Last.fm API responses include MBIDs directly (~80-87% coverage). We don't need MusicBrainz database import to start MBID-first architecture.

### Changes Made
1. `scripts/lastfm_firestore_import.py` - Now MBID-first:
   - Extracts MBIDs from Last.fm API responses
   - Stores `artist_mbids` array for efficient queries
   - Tracks `mbid_count` and `mbid_coverage` per user
   - Keeps `artist_names_lower` for backwards compatibility

2. `backend/services/quiz_service.py` - Now queries both user sources:
   - `_get_collaborative_suggestions()` queries in parallel:
     - `decide_users` (organic quiz users) by artist name
     - `lastfm_users` (Last.fm imported) by artist name (MBID-ready)
   - `_query_collaborative_sources()` handles parallel queries
   - Uses `array_contains_any` for efficient Last.fm user filtering

3. Tests added for Last.fm user integration:
   - `test_queries_both_user_collections`
   - `test_includes_lastfm_users_in_suggestions`
   - `test_lastfm_users_use_array_contains_any`
