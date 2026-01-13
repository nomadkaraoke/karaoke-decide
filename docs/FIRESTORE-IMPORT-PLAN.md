# Firestore Import Plan

## Overview

After mapping Last.fm artists to Spotify IDs, import the processed user data into Firestore's `lastfm_users` collection. This enables the collaborative filtering feature to query 10,000+ users with rich artist preferences.

## Current State

### Existing Implementation

The `QuizService._get_collaborative_suggestions()` method currently:
1. Queries `decide_users` collection (our organic users)
2. Looks at `quiz_artists_known` field (list of artist names)
3. Finds users with ≥3 shared artists
4. Returns suggestions from those users

**Location:** `backend/services/quiz_service.py:502-590`

### Limitation

With no organic users yet, collaborative filtering returns empty results. The Last.fm import solves this by providing 10,000 users with real listening data.

## Data Model

### Collection: `lastfm_users`

Separate collection from `decide_users` to:
- Keep organic user data clean
- Allow different query patterns
- Enable easy removal if needed

**Document ID:** Last.fm username (lowercase, sanitized)

**Schema:**
```typescript
interface LastFmUser {
  // Identity
  lastfm_username: string;       // Original username
  lastfm_url: string;            // Profile URL
  playcount: number;             // Total scrobbles

  // Import metadata
  imported_at: Timestamp;
  source: "lastfm_friends_crawl";

  // Artist data - stored as array for Firestore queries
  top_artists: Array<{
    name: string;                // Last.fm artist name
    playcount: number;           // User's play count for this artist
    spotify_id: string | null;   // Mapped Spotify ID
    spotify_name: string | null; // Spotify's artist name
  }>;

  // Denormalized for efficient querying
  artist_count: number;                    // Length of top_artists
  artist_names_lower: string[];            // Lowercase names for matching
  spotify_artist_ids: string[];            // Non-null Spotify IDs only
  top_artist_names: string[];              // Top 100 artist names for array_contains_any
}
```

### Indexing Strategy

Firestore indexes needed:

```
Collection: lastfm_users
- artist_names_lower (array) - for array_contains_any queries
- spotify_artist_ids (array) - for Spotify ID-based queries
- artist_count (number) - for filtering by data quality
```

**Note:** `array_contains_any` has a limit of 30 values per query.

## Import Pipeline

### Step 1: Load Source Data

```python
# Load from GCS
artist_mapping = gcs.read_json("processed/artist_mapping.json")
user_files = gcs.list_blobs("requests/user.getTopArtists/")
```

### Step 2: Build User Documents

For each cached `user.getTopArtists` response:

```python
def build_lastfm_user_doc(username: str, artists: list, mapping: dict) -> dict:
    top_artists = []
    spotify_ids = []
    artist_names_lower = []

    for artist in artists[:1000]:  # Limit to top 1000
        name = artist["name"]
        name_lower = name.lower()

        # Look up Spotify mapping
        spotify_info = mapping.get("mappings", {}).get(name_lower, {})
        spotify_id = spotify_info.get("spotify_id")

        top_artists.append({
            "name": name,
            "playcount": artist["playcount"],
            "spotify_id": spotify_id,
            "spotify_name": spotify_info.get("spotify_name"),
        })

        artist_names_lower.append(name_lower)
        if spotify_id:
            spotify_ids.append(spotify_id)

    return {
        "lastfm_username": username,
        "imported_at": firestore.SERVER_TIMESTAMP,
        "source": "lastfm_friends_crawl",
        "top_artists": top_artists,
        "artist_count": len(top_artists),
        "artist_names_lower": artist_names_lower,
        "spotify_artist_ids": spotify_ids,
        "top_artist_names": [a["name"] for a in top_artists[:100]],
    }
```

### Step 3: Batch Write to Firestore

Use batched writes for efficiency (500 docs per batch):

```python
batch_size = 500
batch = firestore.batch()
count = 0

for username, doc in user_docs.items():
    ref = firestore.collection("lastfm_users").document(username.lower())
    batch.set(ref, doc)
    count += 1

    if count >= batch_size:
        batch.commit()
        batch = firestore.batch()
        count = 0

if count > 0:
    batch.commit()
```

### Step 4: Create Indexes

Via Firebase Console or CLI:

```bash
firebase firestore:indexes:create --project=nomadkaraoke <<EOF
{
  "indexes": [
    {
      "collectionGroup": "lastfm_users",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "artist_count", "order": "DESCENDING" }
      ]
    }
  ],
  "fieldOverrides": [
    {
      "collectionGroup": "lastfm_users",
      "fieldPath": "artist_names_lower",
      "indexes": [
        { "queryScope": "COLLECTION", "arrayConfig": "CONTAINS" }
      ]
    },
    {
      "collectionGroup": "lastfm_users",
      "fieldPath": "spotify_artist_ids",
      "indexes": [
        { "queryScope": "COLLECTION", "arrayConfig": "CONTAINS" }
      ]
    }
  ]
}
EOF
```

## Query Integration

### Updated `_get_collaborative_suggestions()`

Modify to query both collections:

```python
async def _get_collaborative_suggestions(
    self,
    user_selected_artists: list[str],
    exclude_artists: set[str],
) -> dict[str, list[str]]:
    """Find artists liked by users with similar taste."""

    if len(user_selected_artists) < self.MIN_SHARED_ARTISTS:
        return {}

    user_artists_lower = {a.lower() for a in user_selected_artists}
    exclude_lower = {a.lower() for a in exclude_artists}

    # Query both collections in parallel
    organic_users, lastfm_users = await asyncio.gather(
        self._query_organic_users(),
        self._query_lastfm_users(list(user_artists_lower)[:30]),  # Firestore limit
    )

    all_users = organic_users + lastfm_users

    # Rest of logic unchanged...
```

### New Query Method

```python
async def _query_lastfm_users(
    self,
    artist_names_lower: list[str],
) -> list[dict]:
    """Query Last.fm users who like any of the given artists."""

    if not artist_names_lower:
        return []

    # Use array_contains_any for efficient filtering
    # This returns users who like at least ONE of these artists
    try:
        users = await self.firestore.query_documents(
            "lastfm_users",
            filters=[
                ("artist_names_lower", "array_contains_any", artist_names_lower),
                ("artist_count", ">=", self.MIN_SHARED_ARTISTS),
            ],
            limit=1000,
        )

        # Convert to expected format
        return [
            {
                "quiz_artists_known": u.get("top_artist_names", []),
                "source": "lastfm",
            }
            for u in users
        ]
    except Exception:
        return []
```

## Performance Considerations

### Document Size

Each user document with 1000 artists:
- `top_artists` array: ~100KB (100 bytes × 1000)
- `artist_names_lower`: ~20KB
- `spotify_artist_ids`: ~30KB
- **Total: ~150KB per document**

Firestore limit: 1MB per document ✓

### Query Performance

- `array_contains_any` is efficient with proper indexes
- Limited to 30 values per query (use top artists only)
- 1000 user limit per query is reasonable

### Cost Estimate

| Operation | Count | Cost |
|-----------|-------|------|
| Document writes | 10,000 | ~$0.18 |
| Storage (1.5GB) | Monthly | ~$0.27 |
| Reads per query | ~1000 | ~$0.04 |
| **Initial import** | | **~$0.50** |
| **Monthly storage** | | **~$0.30** |

## Implementation Script

### File: `scripts/lastfm_firestore_import.py`

```
Usage:
  # Run full import
  python scripts/lastfm_firestore_import.py

  # Dry run (show stats, don't write)
  python scripts/lastfm_firestore_import.py --dry-run

  # Show import status
  python scripts/lastfm_firestore_import.py --status

  # Delete all imported users (careful!)
  python scripts/lastfm_firestore_import.py --delete-all
```

### Features:
- Reads from GCS cache and mapping
- Batched Firestore writes (500/batch)
- Progress tracking
- Resumable (skips existing docs)
- Dry run mode for validation

## Validation

After import, verify:

```python
# Check document count
count = firestore.collection("lastfm_users").count().get()
assert count == 10000

# Check sample document
doc = firestore.collection("lastfm_users").document("beveradb").get()
assert doc.exists
assert len(doc.get("top_artists")) > 100
assert doc.get("spotify_artist_ids")  # Has mapped IDs

# Test query
results = firestore.collection("lastfm_users") \
    .where("artist_names_lower", "array_contains_any", ["radiohead", "coldplay"]) \
    .limit(10) \
    .get()
assert len(results) > 0
```

## Rollback Plan

If issues arise:

```python
# Delete all lastfm_users documents
def delete_all_lastfm_users():
    docs = firestore.collection("lastfm_users").stream()
    batch = firestore.batch()
    count = 0

    for doc in docs:
        batch.delete(doc.reference)
        count += 1
        if count >= 500:
            batch.commit()
            batch = firestore.batch()
            count = 0

    if count > 0:
        batch.commit()
```

## Timeline

| Step | Duration | Notes |
|------|----------|-------|
| Build import script | 30 min | Ready before data |
| Run import | 15-20 min | 10K docs, batched |
| Create indexes | 5-10 min | May take time to build |
| Update quiz service | 30 min | Add lastfm query |
| Testing | 30 min | Verify recommendations |
| **Total** | ~2 hours | After mapping complete |

## Next Steps

1. ✅ Plan complete (this document)
2. Build `scripts/lastfm_firestore_import.py`
3. Wait for Spotify mapping to complete
4. Run Firestore import
5. Update `QuizService` to query `lastfm_users`
6. Test collaborative recommendations
