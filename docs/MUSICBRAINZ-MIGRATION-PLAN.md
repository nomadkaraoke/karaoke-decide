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

### Phase 1: Data Foundation ✅ COMPLETE (2026-01-14)
- [x] Create `mb_artists` table from MusicBrainz dump (**2,780,016 artists**)
- [x] Create `mb_artist_tags` table (**693,045 tags**)
- [x] Create `mbid_spotify_mapping` table (**376,231 mappings**)
- [x] Create `mb_artists_normalized` table (pre-joined for fast search)
- [ ] Create `mb_recordings` table (future - not needed for current flow)
- [x] **Discover: Last.fm API already returns MBIDs** (~80-87% coverage)

### Phase 2: API Dual-Support ✅ COMPLETE (2026-01-14)
- [x] Update API models to include both IDs (`ArtistSearchResultMBID`)
- [x] Add MBID lookup endpoints (`search_artists_mbid`, `get_artist_by_mbid`)
- [x] Add MBID batch lookup (`lookup_mbids_by_names`)
- [x] Keep Spotify ID endpoints working (backward compat)
- [x] Update search to return MBID-primary results

### Phase 3: Recommendation Migration ✅ COMPLETE (2026-01-14)
- [x] Update `_get_collaborative_suggestions()` for MBID-first
  - Now queries BOTH `decide_users` AND `lastfm_users` in parallel
  - Uses MBID-based queries when available
  - Falls back to name-based matching for backwards compat
- [x] Update Last.fm import to store MBIDs properly
  - `scripts/lastfm_firestore_import.py` now extracts MBIDs from Last.fm API
  - Stores `artist_mbids` array for MBID-based queries
  - Maintains `artist_names_lower` for backwards compatibility
- [ ] Integrate MLHD+ data (future enhancement)
- [x] Test recommendation quality (144 unit tests passing)

### Phase 4: User Data Migration ✅ COMPLETE (2026-01-14)
- [x] Add `quiz_artist_mbids` field to user documents
- [x] Create migration script (`scripts/migrate_users_to_mbid.py`)
- [x] Migrate all existing users (16/16 = 100%)
- [x] Update quiz submission to store MBIDs
- [x] Enrich `quiz_manual_artists` with MBIDs

### Phase 5: Public API Migration ✅ COMPLETE (2026-01-15)

**Goal:** Update all public API endpoints to return MBID as primary identifier.

**Completed Changes:**

| File | Changes |
|------|---------|
| `backend/api/routes/catalog.py` | ✅ Updated `ArtistSearchResult` model with MBID-first fields (mbid, name, disambiguation, artist_type, tags, spotify_id) |
| `backend/api/routes/catalog.py` | ✅ Updated `/api/catalog/artists` endpoint to use `search_artists_mbid()` with Spotify fallback |
| `backend/api/routes/catalog.py` | ✅ Updated `/api/catalog/artists/index` endpoint to return compact MBID-first format (m, i, n, p fields) |
| `backend/api/routes/quiz.py` | ✅ Updated `QuizArtistResponse` with mbid, spotify_id, tags fields |
| `backend/api/routes/quiz.py` | ✅ Updated `ManualArtistInput` to accept mbid as primary identifier |
| `backend/api/routes/my_data.py` | ✅ Updated `UserArtistResponse` with mbid, spotify_id, tags fields |
| `backend/api/routes/my_data.py` | ✅ Updated `AddArtistRequest` to accept mbid |
| `backend/services/quiz_service.py` | ✅ Updated `ManualArtist` dataclass with mbid field |
| `karaoke_decide/services/bigquery_catalog.py` | ✅ Added `get_artist_index_mbid()` for MBID-first index |
| `karaoke_decide/core/models.py` | ✅ Updated `QuizArtist` with mbid, spotify_id, tags fields |

**API Response Changes:**

```python
# Current response from /api/catalog/artists?q=radiohead
{
    "artists": [
        {
            "artist_id": "4Z8W4fKeB5YxbusRsdQVPb",  # Spotify ID as primary
            "artist_name": "Radiohead",
            "popularity": 79,
            "genres": ["alternative rock", "art rock"]
        }
    ]
}

# Target response (MBID-first with Spotify enrichment)
{
    "artists": [
        {
            "mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711",  # MBID as primary
            "name": "Radiohead",
            "disambiguation": "UK rock band",
            "artist_type": "Group",
            "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb",  # Enrichment (nullable)
            "popularity": 79,  # From Spotify
            "genres": ["alternative rock", "art rock"],  # From Spotify
            "tags": ["alternative", "electronic", "experimental"]  # From MusicBrainz
        }
    ]
}
```

**Backward Compatibility:**
- Keep `artist_id` field as alias for `spotify_id` during transition
- Accept both MBID and Spotify ID in path parameters
- Add `X-API-Version` header for clients to opt into new format

### Phase 6: Frontend Update ✅ COMPLETE (2026-01-15)

**Goal:** Update frontend to use MBID as primary identifier while using Spotify for display enrichment.

**Completed Changes:**

| File | Changes |
|------|---------|
| `frontend/src/types/index.ts` | ✅ Added MBID-first types: `ArtistSearchResult`, `ArtistIndexEntry`, `QuizArtist`, `UserArtist`, `ManualArtistInput` |
| `frontend/src/lib/api.ts` | ✅ Updated API client types for `searchArtists`, `getArtistIndex`, `getSmartArtists`, `quiz.submit` |
| `frontend/src/components/ArtistSearchAutocomplete.tsx` | ✅ Updated `SearchableArtist`, `SelectedArtist` interfaces; use `getArtistUniqueId()` for deduplication |
| `frontend/src/components/QuizArtistCard.tsx` | ✅ Updated `QuizArtist` interface with mbid, spotify_id, tags |
| `frontend/src/hooks/useArtistIndex.ts` | ✅ Updated `IndexedArtist`, `ArtistSearchResult` to include mbid, spotify_id |
| `frontend/src/hooks/useInfiniteArtists.ts` | ✅ Updated `QuizArtist`, `SuggestionReason` types for MBID-first |
| `frontend/src/app/quiz/page.tsx` | ✅ Updated quiz page with `getArtistUniqueId()` helper; map manual artists with mbid in submission |
| `frontend/src/components/MyData/YourArtistsSection.tsx` | ✅ Updated `ArtistSuggestion`, `UserArtist` interfaces for MBID-first |

**TypeScript Interface Changes:**

```typescript
// Current
interface Artist {
    artist_id: string;      // Spotify ID (required)
    artist_name: string;
    popularity: number;
    genres: string[];
}

// Target
interface Artist {
    mbid: string;           // MusicBrainz ID (required, primary)
    name: string;
    disambiguation?: string;
    artist_type?: string;
    spotify_id?: string;    // For Spotify links/images (optional)
    popularity?: number;
    genres?: string[];      // From Spotify
    tags?: string[];        // From MusicBrainz
}
```

**Key Considerations:**
- Artist images: Continue using Spotify CDN via `spotify_id` - MusicBrainz has no images
- Links: Use `spotify_id` for "Open in Spotify" links
- Internal references: Use `mbid` for all internal state, API calls, keys

### Phase 7: Songs/Recordings Migration ✅ COMPLETE (2026-01-15)

**Goal:** Create MusicBrainz recordings table and link to karaoke catalog.

**Why this matters:** Currently songs are 100% Spotify-based. Without this, we can't:
- Match karaoke songs to MusicBrainz recordings
- Use MBID-based song recommendations
- Link user "known songs" to canonical recordings

**Code Implementation:** ✅ COMPLETE (2026-01-14)

- [x] `scripts/musicbrainz_etl.py` - Extended with recording extraction
  - Added `extract-recordings` command (extracts 37.5M recordings + 5.5M ISRCs)
  - Added `load-recordings` and `load-isrcs` commands
- [x] `karaoke_decide/services/bigquery_catalog.py` - Added recording lookup methods
  - `RecordingSearchResult` and `KaraokeRecordingLink` dataclasses
  - `search_recordings()`, `get_recording_by_mbid()`, `lookup_recording_by_isrc()`
  - `lookup_recording_mbid_by_spotify_track_id()`, `get_karaoke_recording_links()`
- [x] `scripts/link_karaoke_to_recordings.py` - New karaoke linking script
  - ISRC-based matching (confidence: 0.95)
  - Exact name matching fallback (confidence: 0.80)
- [x] `tests/unit/test_bigquery_catalog.py` - Added recording tests

**ETL Execution:** ✅ COMPLETE (2026-01-15)

- [x] Extracted 37,530,321 recordings from MusicBrainz dump
- [x] Extracted 5,480,292 ISRCs with recording linkage
- [x] Loaded both tables to BigQuery
- [x] Created `isrc_spotify_mapping` view (17M ISRC matches)
- [x] Ran karaoke linking pipeline

**BigQuery Tables:**

```sql
-- Primary recordings table
CREATE TABLE karaoke_decide.mb_recordings (
    recording_mbid STRING NOT NULL,
    title STRING NOT NULL,
    length_ms INT64,
    artist_credit STRING,
    artist_credit_id INT64,
    disambiguation STRING,
    video BOOLEAN,
    name_normalized STRING,
);

-- ISRC codes for recordings
CREATE TABLE karaoke_decide.mb_recording_isrc (
    recording_mbid STRING NOT NULL,
    isrc STRING NOT NULL,
);

-- Karaoke catalog links
CREATE TABLE karaoke_decide.karaoke_recording_links (
    karaoke_id INT64 NOT NULL,
    recording_mbid STRING,
    spotify_track_id STRING,
    match_method STRING NOT NULL,
    match_confidence FLOAT64,
);
```

**Final Data:**
| Table | Rows | Status |
|-------|------|--------|
| `mb_recordings` | 37,530,321 | ✅ Loaded |
| `mb_recording_isrc` | 5,480,292 | ✅ Loaded |
| `isrc_spotify_mapping` | 17,012,103 | ✅ View created |
| `karaoke_recording_links` | 162,314 | ✅ Linked |

**Karaoke Catalog Coverage:**
- **Total karaoke songs:** 275,809
- **Linked via ISRC:** 96,070 (0.95 confidence)
- **Linked via name match:** 66,244 (0.80 confidence)
- **Total linked:** 162,314 (58.9% coverage)

### Phase 8: Cleanup & Automation (Not Started)

**Goal:** Remove deprecated code paths and set up ongoing data freshness.

**Code Cleanup:**
- [ ] Remove Spotify-only search methods from `bigquery_catalog.py`
- [ ] Remove `artist_id` field from API responses (after frontend migrated)
- [ ] Clean up dual-write logic in quiz service
- [ ] Remove backward-compat name-based queries

**MusicBrainz Refresh Automation:**
- [ ] Set up Cloud Scheduler job to run weekly
- [ ] Download incremental dumps (not full dumps)
- [ ] Update BigQuery tables with new/changed artists
- [ ] Monitor for data quality issues

**Incremental Update Strategy:**
MusicBrainz provides daily "replication packets" (~10MB each) instead of re-downloading 6GB:
```bash
# Daily replication (much faster than full dump)
curl https://metabrainz.org/api/musicbrainz/replication-NNNNNN.tar.bz2
```

**Monitoring:**
- [ ] Alert if MBID coverage drops below threshold
- [ ] Track mapping quality metrics
- [ ] Monitor query performance

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Not all artists have Spotify mapping | Some artists won't have Spotify enrichment | Accept - MB data is still useful |
| MBID format unfamiliar to users | UX confusion | Never show raw MBIDs, always show names |
| Breaking existing user data | Users lose preferences | Dual-write during migration, gradual cutover |
| Performance of MBID lookups | Slower queries | Index properly, cache aggressively |

## Success Criteria

**Phase 1-4 (COMPLETE):**
- [x] All user quiz data stored with MBID as primary key
- [x] Collaborative filtering uses MBID-based queries
- [x] Spotify enrichment available for popular artists (376K mappings)
- [x] No user-facing breakage during migration

**Phase 5-6 (COMPLETE - 2026-01-15):**
- [x] Public API returns MBID as primary identifier
- [x] Frontend uses MBID as primary key for artist references
- [x] Backward compatibility maintained with deprecated field aliases
- [x] All unit tests pass (163 unit, 403 backend)
- [x] TypeScript compiles without errors (excluding pre-existing E2E issues)

**Phase 7 (COMPLETE - 2026-01-15):**
- [x] Songs/recordings migration to MBID-based schema (37.5M recordings)
- [x] Songs/recordings have MBID linkage (5.5M ISRCs)
- [x] Karaoke catalog linked to recordings (162K links, 58.9% coverage)

**Phase 8 (TODO):**
- [ ] MusicBrainz dump refresh automated (weekly)
- [ ] Remove deprecated Spotify-only code paths

## Coordination with MLHD+ Import

✅ MLHD+ import completed by another agent (PR #93):
- 1.5M artist similarity pairs from 583K Last.fm users
- Data keyed by MBID in `mlhd_artist_similarity` table
- Integrated into quiz service for recommendations

## Design Decisions (Resolved)

| Question | Decision | Rationale |
|----------|----------|-----------|
| **Spotify fallback** | Yes, support Spotify-only artists | Some artists (new, indie) may not be in MusicBrainz yet |
| **Image sources** | Continue using Spotify CDN | MusicBrainz has no images; Spotify images are high quality |
| **Genre source** | Use both: Spotify `genres` + MusicBrainz `tags` | Spotify genres are algorithmic but consistent; MB tags are community-curated but noisier |
| **Recording vs Track** | Use recordings only | Works-level abstraction is too complex for karaoke use case |

## Current State Summary (2026-01-15)

**What's DONE (Phases 1-7):**
| Component | Status | Details |
|-----------|--------|---------|
| BigQuery artist tables | ✅ Complete | 2.78M artists, 693K tags, 376K mappings |
| Internal MBID APIs | ✅ Complete | `search_artists_mbid()`, `get_artist_by_mbid()`, etc. |
| Quiz stores MBIDs | ✅ Complete | `quiz_artist_mbids` field in user documents |
| User migration | ✅ Complete | 16/16 users backfilled |
| Collaborative filtering | ✅ Complete | Queries by MBID when available |
| Public API responses | ✅ Complete | MBID-first with Spotify enrichment |
| Frontend | ✅ Complete | TypeScript interfaces updated for MBID |
| BigQuery recordings | ✅ Complete | 37.5M recordings, 5.5M ISRCs |
| Karaoke catalog linking | ✅ Complete | 162K links (58.9% coverage) |

**What's NOT DONE (Phase 8):**
| Component | Status | Impact |
|-----------|--------|--------|
| Data refresh automation | ❌ Not started | Data will become stale |
| Deprecated code cleanup | ❌ Not started | Technical debt |

**Current Data Flow (MBID-First):**
```
User selects "Green Day" in quiz UI
  → Frontend sends: { mbid: "084308bd-..." }
  → Backend uses MBID directly for all operations
  → Backend stores: quiz_artist_mbids: ["084308bd-..."]
  → Backend returns: { mbid: "084308bd-...", spotify_id: "7oPf..." }
  → Frontend uses MBID as key, Spotify for images/links
```

**Target Data Flow (after Phase 8):**
```
User selects "Green Day" in quiz UI
  → Frontend sends: { mbid: "084308bd-..." }
  → Backend uses MBID directly for all operations
  → Backend returns: { mbid: "084308bd-...", spotify_id: "7oPf..." }
  → Frontend uses MBID as key, Spotify for images/links
```

## Next Steps

**Completed:**
1. ~~**Update Last.fm import** - store MBIDs properly~~ ✅ DONE (2026-01-14)
2. ~~**Update collaborative filtering** - query lastfm_users collection~~ ✅ DONE (2026-01-14)
3. ~~**Create MusicBrainz tables**~~ ✅ DONE (2026-01-14)
4. ~~**Create MBID↔Spotify mapping**~~ ✅ DONE (2026-01-14)
5. ~~**Enable MBID-based queries**~~ ✅ DONE (2026-01-14)
6. ~~**Phase 5: Public API Migration**~~ ✅ DONE (2026-01-15)
7. ~~**Phase 6: Frontend Update**~~ ✅ DONE (2026-01-15)
8. ~~**Phase 7: Songs/Recordings**~~ ✅ DONE (2026-01-15)
   - 37.5M recordings loaded to BigQuery
   - 5.5M ISRCs for cross-referencing
   - 162K karaoke songs linked (58.9% coverage)

**Remaining:**
9. **Phase 8: Cleanup & Automation** - Remove deprecated code, set up weekly refresh

**Recommended approach for next agent:**
- Phase 8 can now proceed - all prerequisites are complete
- Consider implementing incremental MusicBrainz replication packets (daily ~10MB) instead of full dumps

## Implementation Notes (2026-01-14)

### Key Discovery
Last.fm API responses include MBIDs directly (~80-87% coverage). We don't need MusicBrainz database import to start MBID-first architecture.

### Session 1 Changes (Earlier)
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

### Session 2 Changes (Complete Migration)

**BigQuery Tables Created:**
| Table | Rows | Description |
|-------|------|-------------|
| `mb_artists` | 2,780,016 | Full MusicBrainz artist catalog |
| `mb_artist_tags` | 693,045 | Community-sourced tags/genres |
| `mbid_spotify_mapping` | 376,231 | MBID to Spotify ID mappings |
| `mb_artists_normalized` | 2,780,016 | Pre-joined for fast search |

**New Scripts:**
- `scripts/musicbrainz_etl.py` - Full ETL pipeline for MusicBrainz dumps
  - Downloads raw dumps from GCS (6.5GB mbdump.tar.bz2)
  - Extracts artist, tags, and mapping data
  - Loads to BigQuery with proper schemas
- `scripts/migrate_users_to_mbid.py` - User migration script
  - Backfills `quiz_artist_mbids` for existing users
  - Enriches `quiz_manual_artists` with MBIDs

**Code Changes:**
1. `karaoke_decide/services/bigquery_catalog.py`:
   - Added `ArtistSearchResultMBID` dataclass
   - Added `search_artists_mbid()` - MBID-first artist search
   - Added `get_artist_by_mbid()` - single artist lookup
   - Added `lookup_mbids_by_names()` - batch name→MBID resolution
   - Added `lookup_mbid_by_spotify_id()` - Spotify→MBID mapping

2. `backend/services/quiz_service.py`:
   - Quiz submission now resolves and stores MBIDs
   - Stores `quiz_artist_mbids` array in user documents
   - Enriches `quiz_manual_artists` with MBID field
   - Collaborative filtering queries by MBID when available

**User Migration:**
- 16/16 users migrated (100%)
- 73 total MBIDs resolved across all users
- Empty arrays stored for users with unresolved artists
