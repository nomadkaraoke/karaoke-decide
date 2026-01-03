# User Data UX Improvements - 2026-01-02

## Summary

Improved the My Data page UX and enhanced data collection from Last.fm to provide better "how well user knows" signals for song/artist ranking.

## Key Changes

### Backend - Data Collection (Phase A)

**Last.fm Client** (`karaoke_decide/services/lastfm.py`):
- Added `get_all_scrobbles()` async generator for full scrobble history pagination
- Added `get_all_top_artists()` with pagination support (up to 1000 artists)
- Added `get_all_top_tracks()` with pagination and actual play counts

**Sync Service** (`backend/services/sync_service.py`):
- Increased limits: LASTFM_TOP_TRACKS_LIMIT = 1000, LASTFM_TOP_ARTISTS_LIMIT = 1000
- Now extracts and stores `playcount` (actual play count from Last.fm)
- Now extracts and stores `rank` (position in user's top list)
- Stores Spotify tracks with rank and time_range for proper ordering

**Data Model**:
- Added `playcount` and `rank` fields to `UserSong` model
- Renamed conceptually: `play_count` is now "sync count" (times seen during sync)
- `playcount` is the actual play count from Last.fm

### Frontend - UX (Phase B)

**My Data Page** (`frontend/src/app/my-data/page.tsx`):
- Reordered sections: Preferences first (most actionable), Connected Services last
- Connected Services now collapsed by default

**ConnectedServicesSection**:
- Shows service logos (Spotify, Last.fm) when collapsed instead of generic count

**YourArtistsSection** → "Artists You Know":
- Renamed from "Your Artists"
- Added playcount pills for Last.fm artists showing actual play counts
- Added popularity pills for Spotify artists
- Sorted by "how well user knows them" (playcount > rank > source)

**YourSongsSection** → "Songs You Know":
- Renamed from "Your Songs"
- Added playcount and rank display pills
- Sorted by playcount (highest first), then rank, then sync count

### API Updates

**GET /api/my/songs** response now includes:
- `playcount` - Actual play count from Last.fm (null for non-Last.fm sources)
- `rank` - Position in user's top list
- `spotify_popularity` - Spotify popularity score (0-100)

## Decisions Made

1. **In-memory sorting over Firestore sorting**: Firestore doesn't support complex multi-field sorting, so we fetch documents and sort in memory. This is fine for typical user libraries (<1000 songs).

2. **Preserve both play_count and playcount**: Kept legacy `play_count` (sync count) for backward compatibility while adding new `playcount` field for actual plays.

3. **Source priority for sorting**: Last.fm > Spotify > Quiz for tie-breaking, since Last.fm provides richer data signals.

4. **Section ordering**: Preferences first because it's most actionable for new users, Connected Services last because it's a one-time setup.

## Future Considerations

1. **Full scrobble history fetch**: The `get_all_scrobbles()` method is ready but not yet used in sync - could enable fetching complete listening history for power users.

2. **Data migration**: Existing users have `play_count` but not `playcount` - consider migration script if needed.

3. **Post-song survey** (Phase D from plan): Planned feature to collect "how singable was this?" feedback after karaoke sessions.

## Files Changed

```
backend/api/routes/recommendations.py
backend/services/recommendation_service.py
backend/services/sync_service.py
backend/services/track_matcher.py
backend/services/user_data_service.py
backend/tests/test_recommendation_service.py
backend/tests/test_sync_service.py
frontend/src/app/my-data/page.tsx
frontend/src/components/MyData/ConnectedServicesSection.tsx
frontend/src/components/MyData/YourArtistsSection.tsx
frontend/src/components/MyData/YourSongsSection.tsx
karaoke_decide/core/models.py
karaoke_decide/services/lastfm.py
docs/API.md
docs/PLAN-USER-DATA-IMPROVEMENTS.md (new)
docs/USER-DATA-VISION.md (new)
```

## Test Results

- All 320 backend tests pass
- Frontend builds successfully
