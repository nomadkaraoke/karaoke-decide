# My Data Page Implementation

**Date:** 2026-01-02
**Status:** Complete

## Summary

Replaced the separate "My Songs" and "Music Services" pages with a unified "My Data" page that provides transparency into all recommendation engine inputs.

## Motivation

- "My Songs" was confusing because Spotify doesn't provide song-level listening history
- Quiz selections adding songs to "My Songs" felt like recommendations, not actual library
- Users needed visibility into what data influences their recommendations
- Services page was only accessible to verified users, creating friction

## What Changed

### New Route: `/my-data`

A unified page with 5 collapsible sections:

1. **Connected Services** - Spotify/Last.fm connection status, sync controls
2. **Your Artists** - All artists from sync + quiz, grouped by source, with add/remove
3. **Your Songs** - Paginated song list with source badges
4. **Preferences** - Editable decade, energy, and genre preferences
5. **Feedback** - Placeholder for future features (love/hide songs, vocal range)

### Backend Changes

**New file:** `/backend/api/routes/my_data.py`
- `GET /api/my/data/summary` - Aggregated counts for page header
- `GET /api/my/data/artists` - Artists from all sources
- `POST /api/my/data/artists` - Add artist manually
- `DELETE /api/my/data/artists/{name}` - Remove artist
- `GET /api/my/data/preferences` - Get preferences
- `PUT /api/my/data/preferences` - Update preferences

**New file:** `/backend/services/user_data_service.py`
- Artist add/remove logic (stores in `users.quiz_artists_known[]`)
- Preferences CRUD

### Frontend Changes

**New page:** `/frontend/src/app/my-data/page.tsx`

**New components in `/frontend/src/components/MyData/`:**
- `ConnectedServicesSection.tsx`
- `YourArtistsSection.tsx`
- `YourSongsSection.tsx`
- `PreferencesSection.tsx`
- `index.ts`

**Modified:**
- `/frontend/src/components/Navigation.tsx` - Single "My Data" link
- `/frontend/src/lib/api.ts` - New typed methods for my data endpoints
- `/frontend/src/components/icons/index.tsx` - Added DatabaseIcon

**Converted to redirects:**
- `/frontend/src/app/my-songs/page.tsx` - Redirects to `/my-data`
- `/frontend/src/app/services/page.tsx` - Redirects to `/my-data`

### Tests

**New file:** `/frontend/e2e/my-data.spec.ts`
- Page loads with all sections
- Services section shows status
- Artists section shows grouped artists
- Preferences section shows/edits values
- Guest users see upgrade prompt
- Redirect tests for old routes

**Updated:**
- `/frontend/e2e/my-songs.spec.ts` - Now tests redirect only
- `/frontend/e2e/services.spec.ts` - Now tests redirect only + Spotify OAuth callbacks

## Data Model

No schema changes needed. Uses existing collections:

| Collection | Use |
|------------|-----|
| `users.quiz_artists_known[]` | Manual + quiz artists |
| `users.quiz_decade_pref` | Decade preference |
| `users.quiz_energy_pref` | Energy preference |
| `users.quiz_genres` | Genre preferences |
| `user_artists` | Synced artists |
| `user_songs` | Songs with source |
| `music_services` | Connection status |

## Files Created

- `/backend/api/routes/my_data.py`
- `/backend/services/user_data_service.py`
- `/backend/tests/test_my_data_routes.py`
- `/backend/tests/test_user_data_service.py`
- `/frontend/src/app/my-data/page.tsx`
- `/frontend/src/components/MyData/ConnectedServicesSection.tsx`
- `/frontend/src/components/MyData/YourArtistsSection.tsx`
- `/frontend/src/components/MyData/YourSongsSection.tsx`
- `/frontend/src/components/MyData/PreferencesSection.tsx`
- `/frontend/src/components/MyData/index.ts`
- `/frontend/e2e/my-data.spec.ts`

## Files Modified

- `/backend/api/routes/__init__.py`
- `/backend/api/deps.py`
- `/frontend/src/lib/api.ts`
- `/frontend/src/components/Navigation.tsx`
- `/frontend/src/components/icons/index.tsx`
- `/frontend/src/app/my-songs/page.tsx` (converted to redirect)
- `/frontend/src/app/services/page.tsx` (converted to redirect)
- `/frontend/e2e/my-songs.spec.ts`
- `/frontend/e2e/services.spec.ts`
- `/docs/README.md`
- `/docs/API.md`

## Key Decisions

1. **Single nav link** - "My Data" accessible to all authenticated users (guests + verified)
2. **Manual artists** - Stored in `users.quiz_artists_known[]` (same as quiz)
3. **No immediate refresh** - Preference changes take effect on next recommendation request
4. **Graceful redirects** - Old URLs redirect to new page for bookmarks/links
5. **Guest experience** - See quiz data + prompt to connect services
