# Known Songs Feature

**Date:** 2026-01-02
**Branch:** feature/known-songs-search
**Status:** Complete, pending PR merge

## Summary

Added a new feature allowing users to manually search for and add songs they already know they like singing. This complements the existing quiz-based artist selection and music service sync by letting users directly specify songs from the karaoke catalog.

## What Was Built

### Backend
- **KnownSongsService** (`backend/services/known_songs_service.py`)
  - `add_known_song()` - Add single song by ID, validates against BigQuery catalog
  - `remove_known_song()` - Remove song (only if source is "known_songs")
  - `get_known_songs()` - List user's known songs with pagination
  - `bulk_add_known_songs()` - Add multiple songs at once

- **API Routes** (`backend/api/routes/known_songs.py`)
  - `GET /api/known-songs` - List known songs
  - `POST /api/known-songs` - Add single song
  - `POST /api/known-songs/bulk` - Bulk add songs
  - `DELETE /api/known-songs/{song_id}` - Remove song

### Frontend
- **Known Songs Page** (`frontend/src/app/known-songs/page.tsx`)
  - Search interface using existing catalog API
  - Known songs list display
  - Add/remove functionality with optimistic UI
  - Link to recommendations page

- **Navigation Update**
  - Added "Add Songs" link with MicrophoneIcon in nav
  - Positioned after "My Data" for logical user flow

### Data Model
- Extended `UserSong.source` literal type to include `"known_songs"`
- Known songs stored in Firestore with source tracking

## Key Decisions

1. **Separate from Quiz**: Known songs are distinct from quiz-selected artists. Quiz adds artist preferences; known songs add specific songs the user knows.

2. **Source Tracking**: Using `source: "known_songs"` allows distinguishing manually-added songs from synced songs (Spotify/Last.fm) or quiz selections.

3. **Catalog Validation**: Songs must exist in the karaoke catalog (BigQuery) before being added. This ensures all known songs have karaoke availability.

4. **No Spotify Lookup**: Unlike My Data's artist add, known songs uses the catalog directly rather than Spotify search. Users search the karaoke catalog to find songs they can actually sing.

## Testing

- 19 backend tests for routes and service layer
- 7 E2E tests for frontend functionality
- All 319 backend tests passing
- All 135 unit tests passing
- Frontend builds successfully

## Integration with Existing Features

- **My Data Page**: Complementary feature - My Data manages artists/preferences, Known Songs manages specific songs
- **Recommendations**: Known songs will be used by recommendation algorithm to improve suggestions
- **User Library**: Known songs appear in user's song library with source "known_songs"

## Files Changed

### New Files
- `backend/services/known_songs_service.py`
- `backend/api/routes/known_songs.py`
- `frontend/src/app/known-songs/page.tsx`
- `backend/tests/test_known_songs.py`
- `frontend/e2e/known-songs.spec.ts`

### Modified Files
- `karaoke_decide/core/models.py` - Added "known_songs" source type
- `backend/api/deps.py` - Added KnownSongsService dependency
- `backend/api/routes/__init__.py` - Registered known_songs router
- `frontend/src/lib/api.ts` - Added knownSongs API client
- `frontend/src/components/Navigation.tsx` - Added nav link
- `frontend/src/components/icons/index.tsx` - Added MicrophoneIcon
