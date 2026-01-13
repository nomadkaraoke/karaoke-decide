# Plan: Collaborative Filtering Recommendations

## Goal
Use aggregated user data to provide richer artist recommendations with reasoning like "Liked by fans of Artist 1, Artist 2, and Artist 3"

## Current State
- Users select artists in quiz step 5 (stored in `decide_users.quiz_artists_known` as Spotify IDs)
- Users can also have synced artists from Spotify/Last.fm (stored in `user_artists` collection)
- Suggestion reasons exist: `similar_artist`, `genre_match`, `decade_match`, `popular_choice`
- We have ~N users with artist preferences in Firestore

## Approach

### New Suggestion Reason Type
Add `fans_also_like` type:
```python
SuggestionReason(
    type="fans_also_like",
    display_text="Liked by fans of Green Day, Blink-182, The Offspring",
    related_to=None  # Could list artist IDs if needed
)
```

### Data Flow
1. **During quiz step 5**: After user selects 3+ artists
2. **Query similar users**: Find users who share ≥3 artists with current user
3. **Aggregate recommendations**: Count which other artists those users like
4. **Filter & rank**: Remove artists user already selected, rank by frequency
5. **Generate reasons**: Include top 3 shared artists in display text

### Implementation Steps

#### 1. Backend: Add collaborative query method to QuizService
```python
async def _get_collaborative_suggestions(
    self,
    user_id: str,
    selected_artist_ids: list[str],
    exclude_ids: set[str],
    limit: int = 20,
) -> list[tuple[str, list[str]]]:
    """
    Find artists liked by users who share tastes with current user.

    Returns: List of (artist_id, shared_artist_names) tuples
    """
```

**Logic:**
1. Query `decide_users` where `quiz_artists_known` overlaps with `selected_artist_ids` (≥3 matches)
2. For matching users, collect their other `quiz_artists_known` entries
3. Count frequency of each artist across similar users
4. Return top N with the shared artists that connected them

#### 2. Backend: Firestore query optimization
Firestore doesn't support array overlap queries directly. Options:

**Option A: Iterate users (simple, works for small user base)**
- Query all `decide_users` with non-empty `quiz_artists_known`
- Filter in Python for ≥3 matches
- Suitable for <10K users

**Option B: Denormalized artist-to-users index (scalable)**
- Create `artist_fans` collection: `{artist_id: str, user_ids: list[str]}`
- Query users by artist, intersect in Python
- More efficient for larger user base

**Recommendation: Start with Option A**, add index if performance becomes an issue.

#### 3. Backend: Integrate into suggestion reason generation
Modify `_generate_suggestion_reason()` priority:
1. `fans_also_like` (if user has ≥3 artists AND we find similar users)
2. `similar_artist` (shares genres with user's selected artists)
3. `genre_match`
4. `decade_match`
5. `popular_choice`

#### 4. Backend: Cache similar user results
- Cache collaborative suggestions per user session (TTL: 10 min)
- Invalidate when user selects new artists
- Use in-memory cache or Firestore subcollection

#### 5. Frontend: Add badge color for new reason type
```typescript
case "fans_also_like":
  return "bg-[var(--brand-purple)]/15 text-[var(--brand-purple)] border-[var(--brand-purple)]/30";
```

### Privacy Considerations
- Never expose other user IDs or personal info
- Only aggregate anonymous counts ("X users who like...")
- Display text mentions artists, not users

### API Changes
No new endpoints needed - enhancements to existing `/api/quiz/artists/smart` response.

Response shape unchanged:
```json
{
  "artists": [
    {
      "artist_id": "...",
      "artist_name": "Sum 41",
      "suggestion_reason": {
        "type": "fans_also_like",
        "display_text": "Liked by fans of Green Day, Blink-182, The Offspring"
      }
    }
  ]
}
```

### Testing Strategy
1. Unit tests for `_get_collaborative_suggestions()` with mocked Firestore
2. Integration test with seeded test users
3. Manual E2E test with real user data

### Performance Estimates
- Query all users: O(N) where N = total users
- In-memory filtering: Fast for <10K users
- If needed later: Add BigQuery export for analytics, Firestore indexes for scale

## Files to Modify
1. `backend/services/quiz_service.py` - Add collaborative query + reason generation
2. `karaoke_decide/core/models.py` - Add `fans_also_like` to SuggestionReason type
3. `frontend/src/components/QuizArtistCard.tsx` - Add purple badge color
4. `backend/tests/test_quiz_service.py` - Add tests for collaborative suggestions
5. `docs/API.md` - Document new reason type

## Estimated Scope
- Backend: ~150 lines
- Frontend: ~5 lines
- Tests: ~100 lines
- Docs: ~20 lines

## Questions to Resolve
1. **Minimum threshold**: Require 3 shared artists, or make configurable?
2. **User count threshold**: Only show "fans_also_like" if ≥5 similar users found? (Avoids "1 user likes this" oddity)
3. **Caching strategy**: In-memory per-request, or persist to Firestore?

## Rollout Plan
1. Implement with feature flag (env var) initially
2. Test with real user data in staging
3. Monitor query performance
4. Enable in production once validated
