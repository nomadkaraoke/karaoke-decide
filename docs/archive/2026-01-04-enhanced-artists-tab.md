# Enhanced Artists Tab - 2026-01-04

## Summary

Redesigned the Artists tab on the Music I Know page to fix data merging issues and add new features for managing artists.

## Problem

1. **Missing Spotify data**: Deduplication bug caused Spotify metadata to be lost when Last.fm data existed (Last.fm has playcount, Spotify doesn't, so Last.fm always "won")
2. **No pagination**: All artists loaded at once (349+ could be slow)
3. **No exclusion**: Could only permanently delete artists, not hide from recommendations
4. **Limited stats display**: Missing genres, popularity, playcount in UI
5. **Verbose UI**: Pill-based layout grouped by source was space-inefficient

## Solution

### Backend Changes

1. **Fixed deduplication** in `user_data_service.py`:
   - `get_all_artists()` now merges data from multiple sources instead of replacing
   - Each artist has `sources: list[str]` showing all sources (spotify, lastfm, quiz)
   - Preserves source-specific fields: `spotify_rank`, `lastfm_playcount`, etc.

2. **Added exclusion support**:
   - New `excluded_artists` array in user document
   - `exclude_artist()` / `include_artist()` methods
   - Exclusions persist through re-syncs

3. **Added pagination**:
   - `page` and `per_page` query parameters
   - Default 100 per page, max 500
   - Response includes `total`, `has_more`

4. **New API endpoints**:
   - `POST /api/my/data/artists/exclude?artist_name=X` - Hide from recommendations
   - `DELETE /api/my/data/artists/exclude?artist_name=X` - Unhide
   - Used query params instead of path params to handle artist names with slashes (e.g., "AC/DC")

5. **Recommendations respect exclusions**:
   - Excluded artists filtered out in `_build_user_context()`

### Frontend Changes

1. **Redesigned ArtistsTab**:
   - Flat list with compact rows instead of grouped pills
   - Source badges per artist showing all sources
   - Stats: Spotify rank, Last.fm playcount, genre, popularity
   - Pagination with "Load More" button
   - Hide/Unhide buttons for synced artists
   - Remove button for manual artists

2. **New icons**: `EyeIcon` and `EyeOffIcon` for hide/unhide actions

## Key Files Modified

- `backend/services/user_data_service.py` - Merging, pagination, exclusion logic
- `backend/api/routes/my_data.py` - New endpoints, response models
- `backend/services/recommendation_service.py` - Respect exclusions
- `frontend/src/app/music-i-know/page.tsx` - ArtistsTab redesign
- `frontend/src/lib/api.ts` - New API types and methods
- `frontend/src/components/icons/index.tsx` - Eye icons

## API Response Format (New)

```json
{
  "artists": [
    {
      "artist_name": "Queen",
      "sources": ["spotify", "lastfm"],
      "spotify_rank": 1,
      "spotify_time_range": "medium_term",
      "lastfm_rank": 5,
      "lastfm_playcount": 847,
      "popularity": 90,
      "genres": ["rock"],
      "is_excluded": false,
      "is_manual": false
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 100,
  "has_more": true
}
```

## Lessons Learned

- **Use query parameters for identifiers with special chars**: Path parameters like `/artists/{name}/exclude` break for "AC/DC". Use query params: `/artists/exclude?artist_name=AC%2FDC`
- **Merge, don't replace**: When deduplicating from multiple sources, merge the data to preserve metadata from all sources
