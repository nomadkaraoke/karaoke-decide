# Plan: Better Artist Selection UX

**Created:** 2026-01-13
**Branch:** feat/sess-20260113-0020-better-artists-selection
**Status:** Draft

## Overview

Improve the Step 5 artist selection experience in the quiz onboarding flow. Currently, when users click artists, the list can feel jarring because:
1. The "Show More Artists" button replaces/adds to the list suddenly
2. Users can't see WHY an artist is being suggested
3. There's no indication of progress or how their selections affect suggestions

We'll implement:
1. **Stable list with graceful appending** - Infinite scroll that appends new artists without disrupting what's visible
2. **Explanation badges** - Show why each artist is suggested ("Based on 90s rock", "Similar to Green Day")
3. **Immediate selection persistence** - Save selections as users tick them so future suggestions improve

## Requirements

### Functional Requirements
- [ ] Artists already visible should NEVER disappear when user makes selections
- [ ] New artists should appear via infinite scroll (not button click that shuffles the list)
- [ ] Each artist card should show a "reason" badge explaining why it's suggested
- [ ] Selections should be saved immediately (no data loss if user leaves page)
- [ ] Next batch of suggestions should be informed by recent selections

### Non-Functional Requirements
- [ ] Smooth scroll-based loading (no janky jumps)
- [ ] Under 200ms to render new batch of artists
- [ ] Graceful degradation if explanation data unavailable

## Technical Approach

### What Data Do We Have for Explanations?

**Available Today:**
1. **Genre data** - `spotify_artist_genres` table has 768 unique genres mapped to artists
2. **Decade inference** - Can be derived from album release dates in `spotify_albums`
3. **User selections** - Genres, decades, and manual artists from earlier quiz steps

**What We Can Build:**
| Reason Type | Data Source | Example Badge |
|-------------|-------------|---------------|
| `genre_match` | User's selected genres + artist's Spotify genres | "Based on punk, rock" |
| `decade_match` | User's selected decades + artist's peak decade | "Popular in the 90s" |
| `similar_artist` | Genre overlap with user's manual artists | "Similar to Green Day" |
| `popular_choice` | High karaoke song count / brand count | "Popular karaoke choice" |

**NOT Available (Stretch Goal):**
- Direct "Related Artists" API from Spotify (requires live API calls)
- Collaborative filtering (no user similarity data yet)
- Audio feature matching (we have the data but no user preferences stored yet)

### Architecture Changes

```
Current Flow:
┌──────────────────────────────────────────────────────────────┐
│ Step 5 loads → API returns 15 artists → Display grid         │
│ "Show More" clicked → API returns 10 more → Append to grid   │
│ Selection → Just local state, doesn't inform next batch      │
└──────────────────────────────────────────────────────────────┘

New Flow:
┌──────────────────────────────────────────────────────────────┐
│ Step 5 loads → API returns 15 artists WITH REASONS           │
│ Scroll near bottom → Fetch next batch (informed by current   │
│                      selections) → Append seamlessly         │
│ Selection → Save immediately to backend → Informs next batch │
└──────────────────────────────────────────────────────────────┘
```

### API Changes

**Modified Response Schema:**
```typescript
interface QuizArtistWithReason {
  name: string;
  artist_id: string;
  song_count: number;
  top_songs: string[];
  genres: string[];
  image_url: string | null;
  // NEW FIELDS:
  suggestion_reason: {
    type: "genre_match" | "decade_match" | "similar_artist" | "popular_choice";
    display_text: string;  // e.g., "Based on punk, rock"
    related_to?: string;   // For similar_artist: the artist name
  };
}
```

### Frontend Changes

1. **Replace button with infinite scroll**
   - Use Intersection Observer to detect when user scrolls near bottom
   - Fetch next batch (10 artists) with current selections as context
   - Append to existing list (never replace)

2. **Add reason badge to QuizArtistCard**
   - Small colored pill below artist name
   - Color coded by reason type
   - Shows explanation text

3. **Immediate selection persistence**
   - On toggle, fire API call to save selection (fire-and-forget)
   - Keep local state optimistic
   - Include newly selected artists in exclude list for next batch

## Implementation Steps

### Phase 1: Backend - Add Explanation Generation (2-3 steps)

1. [ ] **Modify `quiz_service.get_smart_quiz_artists()`** to track WHY each artist was selected
   - Add `_generate_suggestion_reason()` method
   - Return reason type + display text with each artist
   - Priority: genre_match > similar_artist > decade_match > popular_choice

2. [ ] **Update API response model** in `backend/api/routes/quiz.py`
   - Add `suggestion_reason` field to `QuizArtistResponse`
   - Update endpoint to return enriched data

3. [ ] **Add tests** for reason generation logic

### Phase 2: Frontend - Infinite Scroll (3-4 steps)

4. [ ] **Create `useInfiniteArtists` hook**
   - Manages artist list state
   - Handles scroll-triggered loading
   - Tracks which artists have been shown (for exclude)

5. [ ] **Add Intersection Observer** at bottom of artist grid
   - Trigger when last card is ~200px from viewport
   - Show subtle loading indicator while fetching
   - Append new artists with smooth animation

6. [ ] **Update `QuizArtistCard`** to display reason badge
   - Add badge component with color coding
   - Tooltip with full explanation on hover

7. [ ] **Remove "Show More Artists" button** - replaced by infinite scroll

### Phase 3: Immediate Selection Persistence (2 steps)

8. [ ] **Add debounced selection save**
   - On artist toggle, queue selection for save
   - Debounce 500ms to batch rapid selections
   - Fire-and-forget API call (don't block UI)

9. [ ] **Create backend endpoint for incremental save**
   - `POST /api/quiz/selections` - upsert artist selections
   - Lightweight endpoint, just updates Firestore
   - Returns success/failure (frontend ignores)

### Phase 4: Sticky "I'm Done" UI (2 steps)

10. [ ] **Add sticky finish bar at bottom of viewport**
    - Fixed position bar with selection count + "See Recommendations" button
    - Semi-transparent background so content scrolls behind it
    - Always visible regardless of scroll position
    - Collapses to minimal state when user hasn't selected anything yet

11. [ ] **Add encouraging progress indicators**
    - Show "X artists selected" count updating in real-time
    - Optional: milestone celebrations (e.g., "Great start!" at 3 artists)
    - Clear that finishing is optional - "Skip" link always visible

### Phase 5: Polish & Testing (2 steps)

12. [ ] **Add loading states and error handling**
    - Skeleton cards during load
    - Error toast if fetch fails (retry button)
    - "No more suggestions" indicator when exhausted

13. [ ] **E2E tests for new flow**
    - Test infinite scroll loads more artists
    - Test reason badges display correctly
    - Test selections persist across page reload
    - Test sticky bar is always accessible while scrolling

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/services/quiz_service.py` | Modify | Add `_generate_suggestion_reason()` method |
| `backend/api/routes/quiz.py` | Modify | Add `suggestion_reason` to response model |
| `backend/api/routes/quiz.py` | Modify | Add `POST /api/quiz/selections` endpoint |
| `frontend/src/hooks/useInfiniteArtists.ts` | Create | Hook for managing infinite scroll state |
| `frontend/src/components/QuizArtistCard.tsx` | Modify | Add reason badge display |
| `frontend/src/components/ReasonBadge.tsx` | Create | Colored badge component |
| `frontend/src/components/StickyFinishBar.tsx` | Create | Fixed bottom bar with selection count + finish button |
| `frontend/src/app/quiz/page.tsx` | Modify | Replace button with infinite scroll + sticky bar |
| `tests/backend/test_quiz_service.py` | Modify | Add tests for reason generation |
| `frontend/e2e/quiz-infinite-scroll.spec.ts` | Create | E2E tests for new UX |

## Testing Strategy

### Unit Tests
- `quiz_service._generate_suggestion_reason()` returns correct reason types
- Reason priority order is respected (genre > similar > decade > popular)
- Display text is human-readable and correct

### Integration Tests
- `/api/quiz/artists/smart` returns `suggestion_reason` field
- `/api/quiz/selections` persists to Firestore correctly
- Exclude list properly filters out selected artists

### E2E Tests
- Scrolling to bottom loads more artists
- Reason badges are visible on artist cards
- Selecting artist doesn't remove other visible artists
- Selections persist if user navigates away and returns

## Reason Type Priority & Display

| Priority | Type | Trigger Condition | Display Text |
|----------|------|-------------------|--------------|
| 1 | `similar_artist` | Shares 2+ genres with user's manual artist | "Similar to {artist_name}" |
| 2 | `genre_match` | Matches user's selected genres | "Based on {genre1}, {genre2}" |
| 3 | `decade_match` | Peak decade matches user's selection | "Popular in the {decade}s" |
| 4 | `popular_choice` | High brand count, no specific match | "Popular karaoke choice" |

## Open Questions

- [x] Do we have artist similarity data? **No - but we can infer via genre overlap**
- [x] Can we determine artist's "peak decade"? **Yes - via album release dates or infer from genre**
- [x] Should we cap infinite scroll at some point? **No - let users scroll forever, but ensure "I'm Done" is always accessible**
- [ ] Should reason badges be tappable to filter by that reason?

## Stretch Goals (Future Work)

1. **Spotify Related Artists API** - Would require live API integration and rate limiting. Could provide "Fans also like" style suggestions. Requires Spotify API credentials and user OAuth.

2. **Audio Feature Matching** - We have 230M rows of audio features. Could suggest "high energy artists" if user indicates energy preference. Requires storing user's audio preferences first.

3. **Collaborative Filtering** - Track which artists are frequently selected together across all users. Suggest "Users who selected Green Day also selected..." pattern.

## Rollback Plan

All changes are additive to the existing quiz flow:
1. API changes are backward-compatible (new fields added, none removed)
2. Frontend can fall back to button-based loading if infinite scroll fails
3. Immediate persistence is fire-and-forget, doesn't block quiz completion

If issues arise:
1. Disable infinite scroll via feature flag (fall back to button)
2. Hide reason badges via CSS if data quality issues
3. Revert to final quiz submission (disable incremental saves)

## Design Notes

### Sticky "I'm Done" Bar

Always-visible bar at bottom of viewport:

```
┌─────────────────────────────────────────────────────────────┐
│  ✓ 5 artists selected          [See Recommendations →]      │
│                          skip for now                       │
└─────────────────────────────────────────────────────────────┘
```

**States:**
1. **No selections:** "Select artists you know" + muted "Skip" button
2. **Some selections:** "X artists selected" + prominent "See Recommendations" button
3. **Loading:** Button shows spinner, disabled

**Styling:**
- `position: fixed; bottom: 0; left: 0; right: 0;`
- `backdrop-filter: blur(8px);` with semi-transparent background
- Enough padding at bottom of scroll container so last cards aren't hidden behind bar

### Reason Badge Colors (using existing brand palette)

```
similar_artist → var(--brand-pink)     "Similar to X"
genre_match    → var(--brand-blue)     "Based on punk"
decade_match   → var(--brand-yellow)   "Popular in 90s"
popular_choice → var(--text)/60        "Popular choice"
```

### Loading Indicator for Infinite Scroll

Instead of a loading spinner that takes up space, use:
- Subtle pulse animation on the last visible card
- Or a thin progress bar at bottom of scroll area
- Text: "Finding more artists..." (only if > 1s delay)
