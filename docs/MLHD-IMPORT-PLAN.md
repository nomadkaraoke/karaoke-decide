# MusicBrainz Data Import Plan

## Overview

Import data from the **MusicBrainz/MetaBrainz ecosystem** to enhance our recommendation engine:

1. **MLHD+ (Music Listening Histories Dataset+)** - 27 billion listening events from 583,000 Last.fm users for collaborative filtering
2. **MusicBrainz Database** - Rich artist/recording/release metadata, relationships, and genre tags
3. **ListenBrainz APIs** - Pre-computed similar artists and real-time recommendations

This replaces our original plan to crawl 10,000 users via the Last.fm API with a much richer dataset.

---

# Part 1: MLHD+ Listening History Data

## Why MLHD+ Instead of Last.fm API Crawling

| Metric | Last.fm API Crawl | MLHD+ Dataset |
|--------|-------------------|---------------|
| Users | 10,000 | **583,000** (58x more) |
| Listening events | ~10M | **27 billion** |
| Unique artists | Unknown | **555,000** |
| Time to acquire | 7-10 hours | Download time only |
| Rate limits | 1 req/sec | None |
| License | ToS limitations | **CC0 (Public Domain)** |
| Maintenance | Re-crawl needed | Snapshot (March 2023) |

## Dataset Details

**Source:** https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/

**Size:** 272 GB total
- 16 "complete" archives (~15 GB each) = 240 GB
- 16 "partial" archives (~2 GB each) = 32 GB

**Format:** Tab-separated values, one file per user
```
<timestamp>\t<artist_mbid>\t<release_mbid>\t<recording_mbid>
```

**Key characteristics:**
- Timestamps in Unix epoch
- Artist column may contain multiple MBIDs (comma-separated) for collaborations
- Files compressed with zstandard
- Organized in 256 directories by filename prefix

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        MLHD+ Import Pipeline                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐ │
│  │  Download    │───▶│  Extract &       │───▶│  Build Artist          │ │
│  │  MLHD+ Data  │    │  Stream Process  │    │  Co-occurrence Matrix  │ │
│  └──────────────┘    └──────────────────┘    └────────────────────────┘ │
│         │                    │                          │                │
│         ▼                    ▼                          ▼                │
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐ │
│  │  GCS Backup  │    │  Extract Unique  │    │  MBID → Spotify ID     │ │
│  │  (raw data)  │    │  Artist MBIDs    │    │  Mapping               │ │
│  └──────────────┘    └──────────────────┘    └────────────────────────┘ │
│                                                         │                │
│                              ┌───────────────────────────┘               │
│                              ▼                                           │
│                    ┌──────────────────────────────────────────────┐     │
│                    │              Output Tables                    │     │
│                    ├──────────────────────────────────────────────┤     │
│                    │  BigQuery: mlhd_artist_cooccurrence          │     │
│                    │    - artist_a_spotify_id                      │     │
│                    │    - artist_b_spotify_id                      │     │
│                    │    - shared_listeners (count)                 │     │
│                    │    - total_colistens (sum of plays)           │     │
│                    ├──────────────────────────────────────────────┤     │
│                    │  BigQuery: mbid_spotify_mapping               │     │
│                    │    - artist_mbid                              │     │
│                    │    - spotify_artist_id                        │     │
│                    │    - artist_name                              │     │
│                    │    - match_confidence                         │     │
│                    └──────────────────────────────────────────────┘     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Processing Strategy

### Phase 1: Download & Backup (1-2 hours)

1. Download MLHD+ complete archives to local/VM
2. Upload raw archives to GCS for preservation
3. Verify checksums

**Storage:** `gs://nomadkaraoke-mlhd-data/raw/`

### Phase 2: Stream Processing (2-4 hours)

Process data in streaming fashion to avoid memory issues:

```python
def process_user_file(filepath: Path) -> dict[str, int]:
    """Extract artist listen counts from a single user file."""
    artist_counts = defaultdict(int)
    with zstd.open(filepath, 'rt') as f:
        for line in f:
            timestamp, artist_mbids, release_mbid, recording_mbid = line.strip().split('\t')
            # Handle multi-artist recordings
            for mbid in artist_mbids.split(','):
                artist_counts[mbid.strip()] += 1
    return artist_counts
```

For each user, extract:
- Set of unique artists they listen to
- Play counts per artist (for weighting)

### Phase 3: Build Co-occurrence Matrix (1-2 hours)

For collaborative filtering, we need: "users who like artist A also like artist B"

```python
# For each user
user_artists = get_user_artists(user_file)  # Set of artist MBIDs

# Increment co-occurrence for all pairs
for artist_a in user_artists:
    for artist_b in user_artists:
        if artist_a < artist_b:  # Avoid duplicates
            cooccurrence[(artist_a, artist_b)] += 1
```

**Optimization:** Use min-hash / LSH for approximate co-occurrence if exact is too slow.

### Phase 4: MBID → Spotify ID Mapping

Two approaches:

**A. ListenBrainz Labs API (preferred)**
```
POST https://labs.api.listenbrainz.org/spotify-id-from-mbid/json
{"recording_mbid": ["..."]}
```

**B. MusicBrainz Database Dump**
- Download MusicBrainz PostgreSQL dump
- Extract artist external IDs (includes Spotify links where available)
- Build mapping table

**C. Fuzzy Name Matching (fallback)**
- Extract artist names from MusicBrainz
- Match against our `spotify_artists` table by normalized name

### Phase 5: Import to BigQuery

Create final tables for querying:

```sql
-- Artist co-occurrence with Spotify IDs
CREATE TABLE karaoke_decide.mlhd_artist_similarity AS
SELECT
    m1.spotify_artist_id AS artist_a_id,
    m2.spotify_artist_id AS artist_b_id,
    c.shared_listeners,
    c.total_colistens,
    -- Jaccard similarity: intersection / union
    c.shared_listeners / (a_listeners + b_listeners - c.shared_listeners) AS jaccard_similarity
FROM mlhd_cooccurrence c
JOIN mbid_spotify_mapping m1 ON c.artist_a_mbid = m1.artist_mbid
JOIN mbid_spotify_mapping m2 ON c.artist_b_mbid = m2.artist_mbid
WHERE m1.spotify_artist_id IS NOT NULL
  AND m2.spotify_artist_id IS NOT NULL;
```

## Query Pattern for Recommendations

Once imported, we can query:

```python
async def get_similar_artists_mlhd(
    selected_artist_ids: list[str],
    limit: int = 20,
    min_shared_listeners: int = 100,
) -> list[dict]:
    """Get artists that co-occur with selected artists in MLHD+ data."""

    query = """
    SELECT
        CASE
            WHEN artist_a_id IN UNNEST(@selected) THEN artist_b_id
            ELSE artist_a_id
        END AS similar_artist_id,
        SUM(shared_listeners) AS total_shared,
        AVG(jaccard_similarity) AS avg_similarity
    FROM karaoke_decide.mlhd_artist_similarity
    WHERE (artist_a_id IN UNNEST(@selected) OR artist_b_id IN UNNEST(@selected))
      AND shared_listeners >= @min_shared
    GROUP BY similar_artist_id
    HAVING similar_artist_id NOT IN UNNEST(@selected)
    ORDER BY total_shared DESC
    LIMIT @limit
    """

    return await bigquery.query(query, {
        "selected": selected_artist_ids,
        "min_shared": min_shared_listeners,
        "limit": limit,
    })
```

## Alternative: ListenBrainz Similar Artists API

Instead of processing MLHD+ ourselves, we could query ListenBrainz's pre-computed similarity:

```python
async def get_similar_from_listenbrainz(artist_mbid: str) -> list[dict]:
    """Query ListenBrainz Labs API for similar artists."""
    response = await httpx.post(
        "https://labs.api.listenbrainz.org/similar-artists/json",
        json={
            "artist_mbids": [artist_mbid],
            "algorithm": "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
        }
    )
    return response.json()
```

**Pros:** No processing needed, always up-to-date
**Cons:** Requires MBID (need Spotify→MBID mapping), API latency, less control

## Implementation Checklist

### Infrastructure
- [ ] Create GCS bucket `nomadkaraoke-mlhd-data`
- [ ] Set up processing VM (if needed for large dataset)
- [ ] Create BigQuery tables

### Data Pipeline
- [ ] Download script with resume support
- [ ] Streaming processor for user files
- [ ] Co-occurrence matrix builder
- [ ] MBID → Spotify ID mapper

### Integration
- [ ] Update `_get_collaborative_suggestions()` to query MLHD data
- [ ] Add fallback to current Firestore-based approach
- [ ] Add configuration flag to enable MLHD recommendations

### Validation
- [ ] Verify mapping coverage (% of MBIDs with Spotify IDs)
- [ ] Test recommendation quality
- [ ] Compare with current Firestore approach

## Cost Estimate

| Resource | Usage | Est. Cost |
|----------|-------|-----------|
| GCS Storage | ~300GB raw + processed | ~$7/month |
| BigQuery Storage | ~10GB tables | ~$0.20/month |
| BigQuery Queries | Recommendation queries | ~$5/month |
| Processing VM | 4-8 hours (if needed) | ~$2 one-time |
| **Total** | | **~$15/month** |

## Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| Download data | 1-2 hours | Depends on bandwidth |
| Process & extract | 2-4 hours | Can parallelize |
| Build co-occurrence | 1-2 hours | Memory-intensive step |
| MBID mapping | 1-2 hours | API calls or DB lookup |
| Import to BigQuery | 30 min | Bulk load |
| Integration | 1-2 hours | Update recommendation service |
| **Total** | **~8-12 hours** | Can run overnight |

## Comparison with Last.fm API Approach

| Aspect | Last.fm API | MLHD+ |
|--------|-------------|-------|
| Data freshness | Live | March 2023 snapshot |
| User count | 10,000 | 583,000 |
| Control over users | Choose seed users | Random sample |
| Rate limits | 1 req/sec | None |
| Ongoing maintenance | Re-crawl periodically | One-time import |
| API key required | Yes | No |

**Recommendation:** Use MLHD+ as the primary source for collaborative filtering. The 58x larger user base will produce much better recommendations. Supplement with live Last.fm data for freshness if needed later.

## Next Steps

1. Download a sample archive to understand exact format
2. Build streaming processor prototype
3. Estimate MBID → Spotify ID mapping coverage
4. Process full dataset
5. Integrate with recommendation service

---

# Part 2: MusicBrainz Database (Artist/Recording Metadata)

## Overview

The **MusicBrainz Database** is the world's largest open music encyclopedia, containing rich metadata that complements our Spotify data catalog.

**Source:** https://metabrainz.org/datasets/postgres-dumps

**Update frequency:** Twice weekly (Wednesdays and Saturdays)

**License:** CC0 (core data) / CC BY-NC-SA 3.0 (supplementary)

## What MusicBrainz Has That Spotify Doesn't

| Data Type | Spotify | MusicBrainz |
|-----------|---------|-------------|
| Artist relationships | Limited | **Rich** (members, collaborators, tributes, renamed-to) |
| Genre/tags | Basic genres | **Community tags** (768+ hierarchical genres) |
| Artist disambiguation | None | **Full** (e.g., "Queen (UK rock band)" vs "Queen (70s funk)") |
| Recording work links | None | **Compositions** (links covers to originals) |
| ISRCs/ISWCs | Limited | **Comprehensive** industry identifiers |
| Artist areas/countries | Basic | **Detailed** (city-level, with history) |
| Aliases/translations | None | **Full** (artist names in different languages/scripts) |
| Release history | Limited | **Complete** (all editions, pressings, countries) |

## MusicBrainz Entity Model

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     MusicBrainz Core Entities                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │
│  │   Artist    │───▶│  Recording  │───▶│    Work     │                  │
│  │ (performer) │    │ (specific   │    │ (abstract   │                  │
│  │             │    │  version)   │    │ composition)│                  │
│  └─────────────┘    └─────────────┘    └─────────────┘                  │
│         │                  │                                             │
│         ▼                  ▼                                             │
│  ┌─────────────┐    ┌─────────────┐                                     │
│  │   Release   │◀───│   Medium    │                                     │
│  │  (product)  │    │ (disc/side) │                                     │
│  └─────────────┘    └─────────────┘                                     │
│         │                                                                │
│         ▼                                                                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │
│  │Release Group│    │    Label    │    │    Area     │                  │
│  │(album/EP/   │    │ (record co) │    │ (location)  │                  │
│  │ single)     │    │             │    │             │                  │
│  └─────────────┘    └─────────────┘    └─────────────┘                  │
│                                                                          │
│  Cross-cutting: Tags, Ratings, Aliases, Annotations, Relationships      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Key Tables for Our Use Case

### Artist Relationships (High Value)
```
artist_relation:
  - artist_0_mbid, artist_1_mbid, relationship_type
  - Types: member_of, collaboration, tribute, founded, subgroup, renamed
```

**Use case:** "Members of Green Day" → suggest solo projects, side bands

### Community Tags (High Value)
```
artist_tag:
  - artist_mbid, tag_name, count (community votes)

recording_tag:
  - recording_mbid, tag_name, count
```

**Use case:** Community-sourced genre tags often more accurate than Spotify's algorithmic genres

### Work → Recording Links (Medium Value)
```
recording_work:
  - recording_mbid, work_mbid

work:
  - mbid, title, composer_artist_mbid
```

**Use case:** Find all covers of a song, link to original songwriter

### Artist Aliases (Medium Value)
```
artist_alias:
  - artist_mbid, name, locale, type (legal_name, stage_name, etc.)
```

**Use case:** Better search matching ("The Beatles" = "Beatles" = "ビートルズ")

## Import Strategy

### Option A: Full PostgreSQL Import (Comprehensive)

1. Download PostgreSQL dump (~30-50 GB compressed)
2. Restore to local/cloud PostgreSQL
3. Extract relevant tables to BigQuery
4. Join with Spotify data via name matching or external IDs

**Pros:** Full access to all data and relationships
**Cons:** Complex setup, large storage, ongoing sync needed

### Option B: JSON Dump (Simpler)

MusicBrainz also provides JSON dumps with the same data.

**Pros:** Easier to process, no PostgreSQL needed
**Cons:** Same data, just different format

### Option C: Selective API Queries (Lightweight)

Use MusicBrainz API for on-demand lookups:
```
GET https://musicbrainz.org/ws/2/artist/{mbid}?inc=artist-rels+tags
```

**Pros:** No bulk download, always current
**Cons:** Rate limited (1 req/sec), latency

### Recommended Approach

**Hybrid:**
1. Download and process artist relationships once (for "members of", "collaborated with")
2. Download community tags for top artists
3. Use API for on-demand lookups when needed
4. Build MBID ↔ Spotify ID mapping table for joins

## BigQuery Tables to Create

```sql
-- Artist relationships from MusicBrainz
CREATE TABLE karaoke_decide.mb_artist_relationships (
    artist_mbid STRING,
    related_artist_mbid STRING,
    relationship_type STRING,  -- member_of, tribute, collaboration, etc.
    begin_date DATE,
    end_date DATE
);

-- Community genre tags
CREATE TABLE karaoke_decide.mb_artist_tags (
    artist_mbid STRING,
    tag_name STRING,
    vote_count INT64
);

-- Master mapping table
CREATE TABLE karaoke_decide.mbid_spotify_mapping (
    artist_mbid STRING,
    spotify_artist_id STRING,
    artist_name STRING,
    match_method STRING,  -- exact_id, name_match, fuzzy_match
    match_confidence FLOAT64
);
```

---

# Part 3: ListenBrainz APIs (Real-time Recommendations)

## Overview

ListenBrainz provides **pre-computed similar artists** based on their user listening data. This is useful for:
- Quick lookups without processing MLHD+ ourselves
- Always up-to-date (computed from live data)
- Different algorithm than our co-occurrence matrix

## Available APIs

### Similar Artists API
```
POST https://labs.api.listenbrainz.org/similar-artists/json
{
    "artist_mbids": ["8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"],
    "algorithm": "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
}
```

**Response:** List of similar artists with similarity scores

### Spotify ID Mapping APIs
```
POST https://labs.api.listenbrainz.org/spotify-id-from-mbid/json
{"artist_mbid": ["..."]}

POST https://labs.api.listenbrainz.org/mbid-from-spotify-id/json
{"spotify_id": ["..."]}
```

### Artist Metadata API
```
POST https://labs.api.listenbrainz.org/artist-credit-from-artist-mbid/json
{"artist_mbid": ["..."]}
```

## Integration Strategy

```python
async def get_similar_artists_listenbrainz(spotify_artist_id: str) -> list[dict]:
    """Get similar artists using ListenBrainz API."""

    # 1. Convert Spotify ID to MBID
    mbid = await get_mbid_from_spotify_id(spotify_artist_id)
    if not mbid:
        return []

    # 2. Query similar artists
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://labs.api.listenbrainz.org/similar-artists/json",
            json={
                "artist_mbids": [mbid],
                "algorithm": "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
            }
        )
        similar = response.json()

    # 3. Convert MBIDs back to Spotify IDs
    similar_mbids = [s["artist_mbid"] for s in similar]
    spotify_ids = await get_spotify_ids_from_mbids(similar_mbids)

    return [
        {"spotify_id": spotify_ids.get(s["artist_mbid"]), "similarity": s["score"]}
        for s in similar
        if spotify_ids.get(s["artist_mbid"])
    ]
```

---

# Combined Implementation Plan

## Phase 1: MLHD+ Processing (Priority: High)
1. Download MLHD+ complete archives
2. Process to extract artist co-occurrence
3. Build similarity table in BigQuery

## Phase 2: MBID ↔ Spotify Mapping (Priority: High)
1. Use ListenBrainz API for bulk mapping
2. Fall back to name matching for unmapped artists
3. Store mapping table in BigQuery

## Phase 3: MusicBrainz Metadata (Priority: Medium)
1. Download artist relationships from MB dump
2. Extract community tags for top artists
3. Import to BigQuery for enrichment queries

## Phase 4: ListenBrainz API Integration (Priority: Low)
1. Add as secondary recommendation source
2. Use for real-time suggestions
3. Cache results to reduce API calls

## Combined Cost Estimate

| Resource | Usage | Est. Cost |
|----------|-------|-----------|
| GCS Storage (MLHD+) | ~300GB | ~$7/month |
| GCS Storage (MB dump) | ~50GB | ~$1/month |
| BigQuery Storage | ~20GB tables | ~$0.40/month |
| BigQuery Queries | All sources | ~$10/month |
| Processing | One-time | ~$5 |
| **Total** | | **~$25/month** |

## References

- [MLHD+ Documentation](https://musicbrainz.org/doc/MLHD+)
- [MLHD+ Download](https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/)
- [ListenBrainz Similar Artists API](https://labs.api.listenbrainz.org/similar-artists)
- [MusicBrainz Database Schema](https://musicbrainz.org/doc/MusicBrainz_Database/Schema)
- [MusicBrainz PostgreSQL Dumps](https://metabrainz.org/datasets/postgres-dumps)
- [ListenBrainz Labs API](https://labs.api.listenbrainz.org/)
- [Original MLHD Paper](https://simssa.ca/assets/files/gabriel-MLHD-ismir2017.pdf)
- [MLHD Cleanup Blog Post](https://blog.metabrainz.org/2022/10/28/cleaning-up-the-music-listening-histories-dataset/)
