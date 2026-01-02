# Quiz Artist Selection Redesign

**Date:** 2026-01-02
**PR:** #33
**Status:** Deployed to production

## Summary

Redesigned the quiz onboarding from selecting individual songs to selecting artists you know. Also fixed a critical bug where only 2 songs were showing due to a BigQuery JOIN issue.

## Problem

1. **Critical Bug**: Quiz was only showing 2 songs ("My Way" and "My Heart Will Go On") instead of 15. Root cause: BigQuery query JOINed with `spotify_tracks` to get popularity, but Spotify has many versions of popular songs (remasters, compilations), creating duplicate rows that dominated the results.

2. **Poor UX**: Asking users to select individual songs was:
   - Slow (recognizing specific song titles takes cognitive effort)
   - Limited data (each selection = 1 song)
   - Less useful for recommendations

## Solution

### Changed Quiz Flow
- **Before**: "Which songs do you know?" → Select from 15 songs
- **After**: "Which artists do you know?" → Select from 25 artists

### Benefits
- One artist selection = 5 songs added to user's library
- Much richer preference data for recommendations
- Faster, more intuitive UX (easier to recognize artists)
- Each artist card shows song count and top 3 songs preview

### Bug Fix
Removed the problematic Spotify JOIN from `_fetch_quiz_candidates()`. Now using `brand_count` (number of karaoke brands carrying the song) as the popularity proxy instead of Spotify popularity.

## API Changes

New endpoint:
- `GET /api/quiz/artists` - Returns 25 popular karaoke artists with metadata

Updated endpoint:
- `POST /api/quiz/submit` - Now accepts `known_artists: string[]` in addition to `known_song_ids`

## Key Files Modified

- `backend/services/quiz_service.py` - Added `get_quiz_artists()`, `_fetch_artist_candidates()`, `_get_songs_by_artists()`
- `backend/api/routes/quiz.py` - Added `/artists` endpoint, updated submit request model
- `karaoke_decide/core/models.py` - Added `QuizArtist` model
- `frontend/src/app/quiz/page.tsx` - Rewrote for artist selection
- `frontend/src/components/QuizArtistCard.tsx` - New component

## Lessons Learned

- **BigQuery JOINs with large tables can create unexpected duplicates** - The Spotify tracks table has many versions of popular songs. When joining to get metadata, ORDER BY + LIMIT can return mostly duplicates. Solution: Either dedupe in the query or avoid the join if not strictly necessary.

- **Artist-level selection is more efficient than song-level** - For onboarding quizzes, asking about artists gives 5-10x more data per user action while being cognitively easier.
