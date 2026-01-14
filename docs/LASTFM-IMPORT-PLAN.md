# Last.fm User Data Import Plan

## Overview

Import listening history from ~10,000 Last.fm users to bootstrap collaborative filtering recommendations for Karaoke Decide. This data enables "Liked by fans of X, Y, Z" suggestions before we have organic user data.

## Goals

1. **Scale**: 10,000 users with up to 1,000 top artists each
2. **Data preservation**: Never lose raw API responses, persist everything to GCS
3. **Efficiency**: Never make duplicate API calls (full request caching)
4. **Resilience**: Resumable at any point without data loss or duplication
5. **Integration**: Map artists to Spotify IDs for use in our recommendation engine

## Estimated Scope

| Metric | Estimate |
|--------|----------|
| Target users | 10,000 |
| Top artists per user | 1,000 |
| API calls (discovery) | ~15,000 (getInfo + getFriends) |
| API calls (artists) | ~10,000 (getTopArtists) |
| **Total API calls** | ~25,000-30,000 |
| Rate limit (conservative) | 1 request/second |
| **Estimated runtime** | 7-10 hours |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Import Pipeline                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────────┐  │
│  │  Seed    │───▶│   Discovery   │───▶│   Artist Fetcher     │  │
│  │  Users   │    │   (Friends)   │    │   (Top Artists)      │  │
│  └──────────┘    └───────────────┘    └──────────────────────┘  │
│                         │                       │                │
│                         ▼                       ▼                │
│                  ┌─────────────────────────────────────────┐    │
│                  │         Request Cache (GCS)              │    │
│                  │   gs://nomadkaraoke-lastfm-cache/        │    │
│                  │   ├── requests/                          │    │
│                  │   │   ├── user.getInfo/{username}.json   │    │
│                  │   │   ├── user.getFriends/{username}.json│    │
│                  │   │   └── user.getTopArtists/{user}.json │    │
│                  │   └── state/                             │    │
│                  │       ├── discovered_users.json          │    │
│                  │       ├── processed_users.json           │    │
│                  │       └── progress.json                  │    │
│                  └─────────────────────────────────────────┘    │
│                                    │                             │
│                                    ▼                             │
│                  ┌─────────────────────────────────────────┐    │
│                  │         Processor                        │    │
│                  │   - Map artists to Spotify IDs           │    │
│                  │   - Create lastfm_users records          │    │
│                  │   - Aggregate statistics                 │    │
│                  └─────────────────────────────────────────┘    │
│                                    │                             │
│                         ┌─────────┴─────────┐                   │
│                         ▼                   ▼                    │
│                  ┌────────────┐      ┌────────────┐             │
│                  │  Firestore │      │  BigQuery  │             │
│                  │  (live)    │      │  (analytics)│             │
│                  └────────────┘      └────────────┘             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Data Storage Strategy

### 1. Raw API Cache (GCS)

**Bucket**: `gs://nomadkaraoke-lastfm-cache/`

Every API request/response pair is cached to GCS before processing. This ensures:
- We never make the same API call twice
- We never lose raw data even if processing fails
- We can reprocess data without hitting the API again

**Structure**:
```
gs://nomadkaraoke-lastfm-cache/
├── requests/
│   ├── user.getInfo/
│   │   ├── beveradb.json
│   │   ├── radiohead_fan_42.json
│   │   └── ...
│   ├── user.getFriends/
│   │   ├── beveradb.json
│   │   └── ...
│   └── user.getTopArtists/
│       ├── beveradb.json
│       └── ...
├── state/
│   ├── discovered_users.json      # Set of all discovered usernames
│   ├── users_to_process.json      # Queue of users pending artist fetch
│   ├── processed_users.json       # Set of fully processed users
│   └── progress.json              # Current progress metrics
└── exports/
    └── lastfm_users_YYYYMMDD.json # Periodic full exports
```

**Cache File Format** (per request):
```json
{
  "request": {
    "method": "user.getTopArtists",
    "params": {"user": "beveradb", "limit": 1000},
    "timestamp": "2024-01-13T12:00:00Z"
  },
  "response": {
    "status_code": 200,
    "body": { /* raw API response */ },
    "timestamp": "2024-01-13T12:00:01Z"
  }
}
```

### 2. Processed Data (Firestore)

**Collection**: `lastfm_users`

Stores processed user data with Spotify artist mappings. Named clearly to distinguish from `decide_users`.

**Document Schema**:
```typescript
interface LastFmUser {
  // Identity
  id: string;                    // Last.fm username (document ID)
  lastfm_username: string;       // Redundant for queries
  lastfm_playcount: number;      // Total scrobbles
  lastfm_url: string;            // Profile URL

  // Import metadata
  imported_at: Timestamp;
  source: "lastfm_friends_crawl";
  crawl_seed: string;            // Which seed user led to this one
  crawl_depth: number;           // How many hops from seed

  // Artist data (top N with Spotify mappings)
  top_artists: Array<{
    name: string;                // Last.fm artist name
    playcount: number;           // User's play count
    spotify_id: string | null;   // Matched Spotify artist ID
    spotify_name: string | null; // Spotify's version of name
    match_confidence: number;    // 0-1 confidence score
  }>;

  // Aggregated for quick queries
  artist_count: number;          // Total artists in top_artists
  matched_artist_count: number;  // Artists with Spotify IDs
  top_artist_names: string[];    // Just names for quick filtering
}
```

### 3. Analytics Data (BigQuery)

**Table**: `karaoke_decide.lastfm_user_artists`

Flattened view for analytics and bulk processing.

```sql
CREATE TABLE karaoke_decide.lastfm_user_artists (
  lastfm_username STRING,
  artist_name STRING,
  playcount INT64,
  rank INT64,
  spotify_artist_id STRING,
  spotify_artist_name STRING,
  match_confidence FLOAT64,
  imported_at TIMESTAMP
);
```

## Processing Pipeline

### Phase 1: User Discovery

1. Start with seed users (e.g., `beveradb`)
2. For each user:
   - Check GCS cache for `user.getInfo/{username}.json`
   - If not cached: fetch from API, save to GCS
   - Check GCS cache for `user.getFriends/{username}.json`
   - If not cached: fetch from API, save to GCS
   - Add new friends to discovery queue
3. Continue until 10,000 users discovered
4. Save state to `state/discovered_users.json`

**Resumability**:
- Discovery queue persisted to GCS
- Already-discovered users tracked
- Can resume from any point

### Phase 2: Artist Fetching

1. Load discovered users from state
2. For each user not in `processed_users`:
   - Check GCS cache for `user.getTopArtists/{username}.json`
   - If not cached: fetch from API (limit=1000), save to GCS
   - Mark user as processed
   - Periodically save progress to GCS

**Resumability**:
- Processed users tracked separately from discovered
- Can resume fetching without re-fetching already-done users

### Phase 3: Artist Mapping

1. Load all cached `user.getTopArtists` responses from GCS
2. Extract unique artist names
3. Match against `spotify_artists` table in BigQuery:
   ```sql
   SELECT artist_name, artist_id, popularity
   FROM karaoke_decide.spotify_artists
   WHERE LOWER(artist_name) = LOWER(@lastfm_name)
   ```
4. For unmatched artists, try fuzzy matching
5. Cache mapping results for reuse

### Phase 4: Database Import

1. For each processed user:
   - Build `LastFmUser` document with mapped artists
   - Insert to Firestore `lastfm_users` collection
   - Insert rows to BigQuery `lastfm_user_artists` table
2. Create indexes for efficient querying

## Rate Limiting Strategy

**Last.fm ToS**: "Please don't make more than 5 requests per second"

**Our approach**: 1 request per second (conservative)
- Allows for network variance
- Leaves headroom for other users of the API
- Estimated total time: 7-10 hours for 25,000-30,000 requests

**Implementation**:
```python
class RateLimiter:
    def __init__(self, requests_per_second: float = 1.0):
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0

    def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()
```

## Error Handling

### Transient Errors (retry)
- Network timeouts
- 5xx server errors
- Rate limit responses (429)

**Retry strategy**: Exponential backoff (1s, 2s, 4s, 8s, max 60s)

### Permanent Errors (skip and log)
- 404 user not found
- 400 bad request (usually private profile)
- Invalid responses

**Handling**: Log error, mark user as "error", continue with next user

### Critical Errors (pause and alert)
- API key invalid/revoked
- Repeated auth failures
- GCS write failures

**Handling**: Pause processing, save state, alert for manual intervention

## Progress Tracking

**File**: `gs://nomadkaraoke-lastfm-cache/state/progress.json`

```json
{
  "started_at": "2024-01-13T10:00:00Z",
  "last_updated": "2024-01-13T15:30:00Z",
  "phase": "artist_fetching",

  "discovery": {
    "seed_users": ["beveradb"],
    "total_discovered": 10000,
    "queue_remaining": 0,
    "errors": 45
  },

  "artist_fetching": {
    "total_to_process": 10000,
    "processed": 6234,
    "remaining": 3766,
    "errors": 123,
    "estimated_completion": "2024-01-13T18:45:00Z"
  },

  "api_stats": {
    "total_requests": 18702,
    "cache_hits": 2340,
    "errors": 168,
    "requests_per_minute_avg": 58.2
  }
}
```

## Querying for Collaborative Filtering

Once imported, the data enables queries like:

```python
async def get_similar_lastfm_users(
    selected_artists: list[str],
    min_shared: int = 3,
) -> list[dict]:
    """Find Last.fm users who share taste with current user."""

    # Query Firestore for users with overlapping artists
    users = await firestore.query_documents(
        collection="lastfm_users",
        filters=[
            ("top_artist_names", "array_contains_any", selected_artists)
        ],
        limit=1000,
    )

    # Filter to those with sufficient overlap
    similar = []
    selected_lower = {a.lower() for a in selected_artists}

    for user in users:
        their_artists = {a.lower() for a in user["top_artist_names"]}
        shared = selected_lower & their_artists
        if len(shared) >= min_shared:
            similar.append({
                "username": user["lastfm_username"],
                "shared_artists": list(shared),
                "other_artists": [
                    a for a in user["top_artists"]
                    if a["name"].lower() not in selected_lower
                ]
            })

    return similar
```

## Implementation Checklist

### Infrastructure Setup
- [ ] Create GCS bucket `nomadkaraoke-lastfm-cache`
- [ ] Set up bucket lifecycle rules (keep forever, archive after 90 days)
- [ ] Create Firestore collection `lastfm_users`
- [ ] Create BigQuery table `lastfm_user_artists`

### Import Script
- [ ] Implement GCS-backed request cache
- [ ] Implement resumable discovery crawler
- [ ] Implement resumable artist fetcher
- [ ] Implement Spotify artist mapping
- [ ] Implement Firestore/BigQuery importers
- [ ] Add progress tracking and logging
- [ ] Add error handling and retry logic

### Integration
- [ ] Update `_get_collaborative_suggestions()` to query `lastfm_users`
- [ ] Add configuration flag to enable/disable Last.fm data
- [ ] Add monitoring for import progress

### Validation
- [ ] Verify data quality after import
- [ ] Test collaborative suggestions with real data
- [ ] Monitor API usage stays within limits

## Security Considerations

- Last.fm API key stored in Secret Manager (already exists)
- GCS bucket not publicly accessible
- User data treated as PII-adjacent (usernames are public but listening history is personal)
- No Last.fm user credentials stored, only public API data

## Cost Estimate

| Resource | Usage | Est. Cost |
|----------|-------|-----------|
| GCS Storage | ~500MB raw JSON | ~$0.01/month |
| GCS Operations | ~50,000 writes | ~$0.25 |
| Firestore | 10,000 docs | ~$0.18 |
| BigQuery | ~10M rows | ~$0.50 |
| **Total** | | **~$1/month** |

## Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| Infrastructure setup | 1 hour | GCS bucket, collections |
| Script development | 2-3 hours | Build resumable importer |
| Discovery phase | 2-3 hours | Find 10,000 users |
| Artist fetching | 4-6 hours | Fetch top 1,000 artists each |
| Processing | 1 hour | Map to Spotify, import |
| **Total** | **~12 hours** | Can run overnight |

## Next Steps

1. Create GCS bucket and set up infrastructure
2. Build the import script with caching and resumability
3. Start discovery phase with seed user `beveradb`
4. Let artist fetching run (can pause/resume as needed)
5. Process and import once complete
6. Update collaborative filtering to use imported data
