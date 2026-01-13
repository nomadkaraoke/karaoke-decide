# Spotify Artist Mapping Plan

## Overview

After importing Last.fm user data (10,000 users × 1,000 top artists), we need to map Last.fm artist names to Spotify artist IDs. This enables integration with our recommendation engine which uses Spotify IDs as primary keys.

## Data Sources

| Source | Count | Notes |
|--------|-------|-------|
| Last.fm users | 10,000 | Cached in GCS |
| Artists per user | up to 1,000 | From `user.getTopArtists` |
| Unique artists (est.) | ~500K-1M | After deduplication |
| Spotify artists | 15.4M | In BigQuery |

## Mapping Strategy

### Phase 1: Extract Unique Artists

1. Read all `user.getTopArtists/*.json` files from GCS cache
2. Extract artist names (already have `name`, `playcount`, `mbid`)
3. Deduplicate by lowercase name
4. Track frequency (how many users like each artist)

**Output:** `gs://nomadkaraoke-lastfm-cache/processed/unique_artists.json`
```json
{
  "radiohead": {
    "canonical_name": "Radiohead",
    "user_count": 3456,
    "total_plays": 1234567,
    "mbid": "a74b1b7f-71a5-4011-9441-d0b5e4122711"
  }
}
```

### Phase 2: Exact Matching (BigQuery)

Query BigQuery for exact case-insensitive matches:

```sql
-- Create temp table of Last.fm artists
CREATE TEMP TABLE lastfm_artists AS
SELECT DISTINCT LOWER(artist_name) as name_lower, artist_name
FROM UNNEST(@artist_names) as artist_name;

-- Match against Spotify
SELECT
  l.artist_name as lastfm_name,
  s.artist_id as spotify_id,
  s.artist_name as spotify_name,
  s.popularity,
  s.followers_total
FROM lastfm_artists l
JOIN `karaoke_decide.spotify_artists` s
  ON LOWER(s.artist_name) = l.name_lower
ORDER BY s.popularity DESC;
```

**Expected match rate:** 70-80% (most popular artists have consistent naming)

### Phase 3: Fuzzy Matching (for unmatched)

For artists not matched exactly, try fuzzy approaches:

1. **Normalization**: Remove special characters, "The ", etc.
   - "The Beatles" → "Beatles"
   - "AC/DC" → "ACDC"

2. **MusicBrainz ID matching**: Some Last.fm responses include `mbid`
   - Could cross-reference if we have MB→Spotify mapping

3. **Levenshtein distance**: For close matches (edit distance ≤ 2)
   - "Radiohead" vs "Radio Head"

4. **Token-based**: Match on sorted word tokens
   - "Green Day" matches "day green" (unlikely but handles reordering)

**Expected additional matches:** 10-15%

### Phase 4: Manual/Skip

Some artists won't match:
- Last.fm-only artists (not on Spotify)
- Misspellings in Last.fm data
- Regional/local artists

**Action:** Log unmatched artists, skip for now, can add manual mappings later.

## Mapping Output

**File:** `gs://nomadkaraoke-lastfm-cache/processed/artist_mapping.json`

```json
{
  "mappings": {
    "radiohead": {
      "lastfm_name": "Radiohead",
      "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "spotify_name": "Radiohead",
      "match_type": "exact",
      "confidence": 1.0,
      "popularity": 78
    },
    "the beatles": {
      "lastfm_name": "The Beatles",
      "spotify_id": "3WrFJ7ztbogyGnTHbHJFl2",
      "spotify_name": "The Beatles",
      "match_type": "exact",
      "confidence": 1.0,
      "popularity": 86
    }
  },
  "unmatched": ["some obscure artist", "local band name"],
  "stats": {
    "total_artists": 500000,
    "exact_matches": 400000,
    "fuzzy_matches": 50000,
    "unmatched": 50000,
    "match_rate": 0.90
  }
}
```

## Implementation

### Script: `scripts/lastfm_spotify_mapping.py`

```
Usage:
  # Run full mapping
  python scripts/lastfm_spotify_mapping.py

  # Show mapping stats
  python scripts/lastfm_spotify_mapping.py --status

  # Export mapping to CSV
  python scripts/lastfm_spotify_mapping.py --export mapping.csv
```

### Steps:

1. **Load cached responses** from GCS
2. **Extract unique artists** with frequency counts
3. **Batch query BigQuery** (1000 artists per query for efficiency)
4. **Apply fuzzy matching** for unmatched
5. **Save mapping** to GCS
6. **Report statistics**

### Performance Considerations

- **Batch BigQuery queries**: 1000 artists per query, ~500-1000 queries total
- **BigQuery cost**: ~$0.005 per query × 1000 = ~$5 total
- **Runtime**: ~30 minutes (mostly BigQuery latency)
- **Cache mapping**: Once computed, reuse for all users

## Next Steps After Mapping

### Phase 5: Build lastfm_users Collection

With mappings complete, create Firestore documents:

```python
for username in processed_users:
    artists = load_cached_artists(username)
    mapped_artists = [
        {
            "name": a["name"],
            "playcount": a["playcount"],
            "spotify_id": mapping.get(a["name"].lower(), {}).get("spotify_id"),
            "spotify_name": mapping.get(a["name"].lower(), {}).get("spotify_name"),
        }
        for a in artists
    ]

    firestore.set("lastfm_users", username, {
        "lastfm_username": username,
        "top_artists": mapped_artists,
        "imported_at": now(),
    })
```

### Phase 6: Query Integration

Update `_get_collaborative_suggestions()` to query `lastfm_users`:

```python
# Find users who share artists with current quiz taker
similar_users = db.query(
    "lastfm_users",
    where("top_artist_ids", "array_contains_any", user_selected_artist_ids)
)

# Get artists those users like that current user hasn't selected
for user in similar_users:
    for artist in user.top_artists:
        if artist.spotify_id not in user_selected_ids:
            suggestions.add(artist)
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Low match rate | Fewer usable recommendations | Accept 80%+ match rate as sufficient |
| BigQuery costs | Budget overrun | Batch efficiently, estimate before running |
| Name ambiguity | Wrong artist matched | Use popularity ranking to prefer well-known artists |
| API rate limits | N/A | All data already cached, no API calls needed |

## Success Criteria

- **Match rate ≥ 80%** of unique artists
- **Top 1000 artists** (by user count) have ≥ 95% match rate
- **Mapping completes** in under 1 hour
- **No data loss** - all mappings persisted to GCS
