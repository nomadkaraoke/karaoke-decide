# MLHD+ Data Import Plan

## Overview

Import the **MLHD+ (Music Listening Histories Dataset+)** to power collaborative filtering recommendations. This dataset contains 27 billion listening events from 583,000 Last.fm users - vastly larger than our original plan to crawl 10,000 users via the Last.fm API.

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

## References

- [MLHD+ Documentation](https://musicbrainz.org/doc/MLHD+)
- [MLHD+ Download](https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/)
- [ListenBrainz Similar Artists API](https://labs.api.listenbrainz.org/similar-artists)
- [Original MLHD Paper](https://simssa.ca/assets/files/gabriel-MLHD-ismir2017.pdf)
- [MLHD Cleanup Blog Post](https://blog.metabrainz.org/2022/10/28/cleaning-up-the-music-listening-histories-dataset/)
