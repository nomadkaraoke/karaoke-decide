# Implementation Plan: User Data Improvements

> Based on UX review feedback captured in [USER-DATA-VISION.md](USER-DATA-VISION.md)

## Overview

This plan addresses major gaps in how we collect, store, and display user music data. The goal is to surface accurate, comprehensive data about what users listen to so we can provide better karaoke recommendations.

## Part 1: Backend - Data Collection Fixes (Priority: Critical)

### 1.1 Last.fm Full Scrobble History Sync

**Problem:** We fetch only 900 tracks (top 500 + loved 200 + recent 200). Users like the product owner have 219k+ scrobbles.

**Solution:** Implement full scrobble history fetch using `user.getRecentTracks` with pagination.

**Files to modify:**
- `karaoke_decide/services/lastfm.py` - Add `get_all_scrobbles()` method
- `backend/services/sync_service.py` - Replace `_fetch_lastfm_tracks()` implementation

**New Last.fm client method:**
```python
async def get_all_scrobbles(
    self,
    username: str,
    from_timestamp: int | None = None,
    progress_callback: Callable | None = None,
) -> AsyncGenerator[dict, None]:
    """Fetch ALL scrobbles for a user with pagination.

    Args:
        username: Last.fm username
        from_timestamp: Optional UNIX timestamp to fetch scrobbles after (for incremental sync)
        progress_callback: Optional callback(total, fetched) for progress updates

    Yields:
        Track dict with artist, title, playcount, timestamp
    """
    page = 1
    total_pages = None

    while True:
        response = await self._api_request(
            "user.getrecenttracks",
            {
                "user": username,
                "limit": 200,  # Max per page
                "page": page,
                "from": from_timestamp,
                "extended": 1,
            },
        )

        tracks_data = response.get("recenttracks", {})
        tracks = tracks_data.get("track", [])

        # Get pagination info from @attr
        attr = tracks_data.get("@attr", {})
        total_pages = int(attr.get("totalPages", 1))
        total = int(attr.get("total", 0))

        if progress_callback:
            await progress_callback(total=total, fetched=page * 200)

        for track in tracks:
            if track.get("@attr", {}).get("nowplaying"):
                continue  # Skip currently playing
            yield track

        if page >= total_pages:
            break
        page += 1
```

**Data to extract from each scrobble:**
- `artist` (name)
- `title` (name)
- `album` (if available)
- `timestamp` (date.uts)
- Aggregate into play counts per track

**Sync duration consideration:**
- 219k scrobbles ÷ 200 per page = ~1,100 API calls
- At ~0.5s per call = ~10 minutes
- Progress callback enables UI updates

### 1.2 Last.fm Top Artists with Full Pagination

**Problem:** We fetch only 50 artists per period. Last.fm can return 1000+.

**Solution:** Paginate through all top artists.

**Files to modify:**
- `backend/services/sync_service.py` - Update `_fetch_and_store_lastfm_artists()`

**Changes:**
```python
# Current: limit=50, no pagination
# New: limit=200, paginate until empty

async def _fetch_and_store_lastfm_artists(self, user_id: str, username: str) -> int:
    stored = 0
    now = datetime.now(UTC)

    # Fetch top artists for "overall" period with pagination
    page = 1
    while True:
        response = await self.lastfm.get_top_artists(
            username, period="overall", limit=200, page=page
        )
        artists = response.get("topartists", {}).get("artist", [])

        if not artists:
            break

        for rank, artist in enumerate(artists, start=(page - 1) * 200 + 1):
            # ... store artist with rank and playcount
            stored += 1

        page += 1
        if len(artists) < 200:  # Last page
            break

    return stored
```

### 1.3 Store Actual Play Counts from Last.fm

**Problem:** `play_count` field is "sync count", not actual listens. Last.fm provides real play counts.

**Solution:** Extract and store `playcount` from Last.fm responses.

**Files to modify:**
- `backend/services/sync_service.py` - Update `_extract_lastfm_track_info()` and storage

**Track data model update:**
```python
# In user_tracks collection (rename from user_songs for clarity?)
{
    "playcount": 47,  # Actual Last.fm scrobble count for this track
    "first_scrobbled_at": "2008-05-15T...",  # Earliest scrobble
    "last_scrobbled_at": "2025-12-28T...",   # Most recent scrobble
    "sync_count": 3,  # Renamed from play_count - how many syncs saw this track
}
```

### 1.4 Spotify Top Tracks with Rank Preservation

**Problem:** We fetch top tracks but lose rank information - everything gets deduplicated.

**Solution:** Store top tracks separately with rank metadata.

**Files to modify:**
- `backend/services/sync_service.py` - Add `_fetch_and_store_spotify_top_tracks()`

**New storage:**
```python
# user_top_tracks collection (separate from user_songs)
{
    "user_id": "xxx",
    "source": "spotify",
    "artist": "Taylor Swift",
    "title": "Anti-Hero",
    "rank": 3,
    "time_range": "medium_term",
    "spotify_popularity": 89,
    "updated_at": "..."
}
```

---

## Part 2: Data Model Refactoring

### 2.1 Separate Collections for Different Data Types

**Current:** Everything in `user_songs`
**Proposed:** Split by purpose

| Collection | Purpose | Key Fields |
|------------|---------|------------|
| `user_artists` | Artists user knows | source, name, rank, playcount |
| `user_tracks` | Tracks user has listened to | source, artist, title, playcount, timestamps |
| `user_top_tracks` | Ranked top tracks from services | source, rank, time_range |
| `known_songs` | Manually added songs user knows they like singing | song_id, notes |
| `sung_songs` | Songs user has actually sung | song_id, rating, feedback, sung_at |

### 2.2 Migration Path

1. Keep existing `user_songs` for now (backward compatibility)
2. Add new collections alongside
3. Update frontend to read from new collections
4. Deprecate old collection after migration

---

## Part 3: Frontend - My Data Page Improvements

### 3.1 Section Reordering

**New order:**
1. Preferences (most actionable)
2. Artists You Know
3. Songs You Know
4. Connected Services (collapsed by default)

### 3.2 Connected Services Collapsed State

**When collapsed, show:**
- Horizontal row of service logos (Spotify green, Last.fm red)
- Brief text: "Spotify, Last.fm connected"
- Chevron to expand

**When expanded:**
- Full connection details
- Sync status
- Last sync time
- Connect/disconnect buttons

### 3.3 Artists You Know Section

**Rename from:** "Your Artists"
**Display:**
- Combined list from all sources, deduplicated by name
- Sorted by Last.fm playcount (if available) or Spotify rank
- Each row shows:
  - Artist name
  - Source badge (Spotify/Last.fm/Manual)
  - Playcount pill (if Last.fm): "1,234 plays"
  - Popularity pill (if Spotify): "Pop: 87"
- Manual add form at bottom

### 3.4 Songs You Know Section

**Rename from:** "Your Songs"
**Display:**
- Combined list from all sources
- Sorted by Last.fm playcount (if available) or rank
- Each row shows:
  - Song title - Artist
  - Source badge
  - Playcount pill (if available)
  - Karaoke available indicator

---

## Part 4: Future - Songs You Enjoy Singing

### 4.1 Post-Song Survey (Future Phase)

Track what users have actually sung:

**Data model:**
```python
# sung_songs collection
{
    "user_id": "xxx",
    "song_id": "catalog:12345",
    "sung_at": ["2025-12-25T...", "2025-12-28T..."],
    "times_sung": 2,
    "rating": 4,  # 1-5
    "would_sing_again": True,
    "feedback": {
        "vocal_range": "comfortable",  # or "too_high", "too_low"
        "difficulty": "medium",
        "crowd_response": "loved_it",
        "notes": "Great for warming up"
    }
}
```

**UI integration points:**
- "Sing it!" button on song cards
- Post-song popup: "How was it?"
- "My Favorites" filter in recommendations

---

## Implementation Phases

### Phase A: Critical Backend Fixes (This PR)
1. [ ] Last.fm full scrobble history fetch
2. [ ] Last.fm artists pagination (1000+)
3. [ ] Extract real play counts from Last.fm
4. [ ] Store Spotify top tracks with rank
5. [ ] Update data model/collections

### Phase B: Frontend My Data Improvements (This PR)
1. [ ] Reorder sections (Preferences first)
2. [ ] Connected Services collapsed by default
3. [ ] Rename sections ("Artists You Know", "Songs You Know")
4. [ ] Add playcount/popularity pills
5. [ ] Combined sorted lists by "how well known"

### Phase C: Data Integrity (This PR)
1. [ ] Rename `play_count` to `sync_count` in existing data
2. [ ] Add migration for new `playcount` field
3. [ ] Update API responses to include real play counts

### Phase D: Post-Song Survey (Future PR)
1. [ ] `sung_songs` collection
2. [ ] "How was it?" UI
3. [ ] "My Favorites" integration

---

## Estimated Effort

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase A | 2-3 days | None |
| Phase B | 1-2 days | Phase A (for real data) |
| Phase C | 0.5 day | Phase A |
| Phase D | 2-3 days | Phases A-C |

---

## Success Criteria

1. **Last.fm users see accurate data:**
   - Full artist list (1000+ if applicable)
   - Full track list with real play counts
   - Artists/songs sorted by actual listen frequency

2. **Spotify users see available data:**
   - Top artists (150)
   - Top tracks (150) with rank
   - Clear indication this is "top" not "all"

3. **My Data page is informative:**
   - Users understand what data we have
   - Users can identify gaps
   - Users can manually supplement

4. **Performance acceptable:**
   - Full Last.fm sync completes in <15 minutes
   - Progress shown to user
   - Incremental sync for subsequent runs
