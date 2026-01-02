# Enhanced Recommendations System

**Date:** 2026-01-01
**PRs:** #25, #26, #28, #29

## Summary

Implemented categorized recommendations with artist diversity, rich filters, and "Create Your Own Karaoke" section for songs without existing karaoke versions.

## What Changed

### New API Endpoint

`GET /api/my/recommendations/categorized` - Returns recommendations in three categories:
- **From Artists You Know** - Karaoke songs by artists in user's library (max 15)
- **Create Your Own Karaoke** - Songs without karaoke versions from user's library (max 10)
- **Crowd Pleasers** - Popular karaoke songs for discovery (max 10)

### Rich Filters

New query parameters for filtering recommendations:
- `has_karaoke` - Filter by karaoke availability (true/false/null)
- `min_popularity` / `max_popularity` - Spotify popularity range (0-100)
- `exclude_explicit` - Hide explicit content
- `min_duration_ms` / `max_duration_ms` - Song duration range
- `classics_only` - Only songs with brand_count >= 20

### Backend Changes

1. **Artist Diversity** - Max 3 songs per artist to prevent dominance by Elton John/Frank Sinatra
2. **Diminishing Returns Scoring** - sqrt curve for popularity, capped brand count benefit
3. **Unmatched Track Storage** - Sync now stores tracks without karaoke match for "Create Your Own" section
4. **is_classic Flag** - Songs with 20+ karaoke brands marked as classics

### Frontend Changes

1. **Categorized UI** - Three collapsible sections with recommendations
2. **Rich Filter Panel** - Radio buttons and checkboxes for filtering
3. **Generate-Only Cards** - Cyan styling for songs without karaoke, direct "Generate" button

### Bug Fixes

- **PR #26** - Fixed `is_classic` always being false (missing assignment in ScoredSong constructor)
- **PR #28** - Fixed `spotify_popularity` None in `_calculate_score` (dict.get() returns None if key exists with None value)
- **PR #29** - Fixed same issue in `ScoredSong` creation

### Infrastructure

- Created Firestore composite index for `has_karaoke_version` + `user_id` + `play_count` query

## Files Modified

| File | Changes |
|------|---------|
| `backend/services/recommendation_service.py` | Artist diversity, categorized method, scoring changes, None handling |
| `backend/services/sync_service.py` | Store unmatched tracks with `has_karaoke_version: false` |
| `backend/api/routes/recommendations.py` | New categorized endpoint with filter params |
| `karaoke_decide/core/models.py` | New fields: `has_karaoke_version`, `is_classic`, `duration_ms`, `explicit` |
| `frontend/src/types/index.ts` | New TypeScript types for categorized response |
| `frontend/src/lib/api.ts` | New `getCategorizedRecommendations()` method |
| `frontend/src/app/recommendations/page.tsx` | Complete rewrite with categorized UI and filters |
| `frontend/src/components/RecommendationCard.tsx` | Generate-only card styling |

## Key Learnings

1. **dict.get() with None value** - `doc.get("key", default)` returns None if key exists but has None value, not the default. Must explicitly check: `value if value is not None else default`

2. **Firestore composite indexes** - Complex queries with multiple filters require composite indexes. Create via gcloud or Firebase console.

## Testing

- All 135 unit tests pass
- All backend tests pass
- CI passed on all PRs
- Manual verification: 35 total recommendations (15 + 10 + 10) with correct categorization
