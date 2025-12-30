# Plan: Phase 4 - Quiz & Recommendations

## Overview

Implement quiz-based onboarding for data-light users and a recommendation algorithm (v1) that surfaces relevant karaoke songs based on listening history and quiz responses. This phase enables users without streaming data to still get personalized recommendations.

## Requirements

### Functional Requirements

1. **Quiz Song Selection**
   - Endpoint to fetch quiz songs (popular karaoke songs users likely know)
   - Returns 10-20 songs stratified by decade and genre
   - Songs must have high brand count (covered by many karaoke brands)
   - Include artist, title, and optionally a preview URL

2. **Quiz Submission**
   - Accept list of song IDs the user recognized/knows
   - Store optional decade preference
   - Store optional energy preference (chill/medium/high)
   - Create UserSong records for selected songs (source: "quiz")
   - Update user profile with quiz metadata

3. **Recommendation Algorithm v1**
   - Input: User's listening history (UserSongs) + quiz responses
   - Output: Ranked list of karaoke songs they might enjoy
   - Scoring factors:
     - Songs from artists they've listened to
     - Songs similar to ones they know (same genre/decade)
     - Karaoke popularity (brand count)
     - General popularity (Spotify popularity score)
   - Return explanation for each recommendation ("You listen to this artist")

4. **Filter/Sort for Discovery**
   - Filter by decade (80s, 90s, 2000s, 2010s, 2020s)
   - Filter by energy level (using Spotify audio features if available)
   - Filter by "songs I know" (only from user's library)
   - Sort by: relevance, popularity, artist name, title
   - Pagination support

### Non-Functional Requirements

- Response time < 500ms for quiz and recommendations
- Quiz songs cached (don't recompute every request)
- Recommendations cached per-user (invalidate on sync)
- 70%+ test coverage for new code
- All endpoints documented in API.md

## Technical Approach

### Quiz Song Selection

1. **Pre-compute quiz candidates** via BigQuery:
   - Join `karaokenerds_raw` with `spotify_tracks` on normalized artist+title
   - Filter: brand_count >= 5 (available on many karaoke systems)
   - Filter: spotify_popularity >= 50 (well-known songs)
   - Group by decade to ensure diversity
   - Store top 100 candidates in a "quiz_songs" Firestore collection or cache

2. **Endpoint returns stratified sample**:
   - 2-3 songs from each decade (80s, 90s, 2000s, 2010s, 2020s)
   - Randomize within strata for variety
   - Include song metadata for display

### Quiz Submission Flow

1. Accept `POST /api/quiz/submit` with:
   - `known_song_ids: list[str]` - songs user recognized
   - `decade_preference: str | None` - preferred decade
   - `energy_preference: str | None` - chill/medium/high

2. For each known song, create UserSong with:
   - `source: "quiz"`
   - `play_count: 1` (implicit "knows it")
   - Link to karaoke catalog

3. Update User profile:
   - `quiz_completed_at: datetime`
   - `quiz_songs_known: list[str]`
   - `quiz_decade_pref: str | None`
   - `quiz_energy_pref: str | None`

### Recommendation Algorithm v1

**Scoring Formula:**
```
score = (
    artist_match_weight * is_known_artist +
    genre_match_weight * genre_similarity +
    decade_match_weight * decade_preference_match +
    popularity_weight * normalized_popularity +
    karaoke_availability_weight * brand_count_score
)
```

**Initial Weights:**
- artist_match: 0.35 (highest - they know this artist)
- popularity: 0.25 (crowd pleasers)
- karaoke_availability: 0.20 (easy to find at venues)
- genre_match: 0.12
- decade_match: 0.08

**Implementation:**
1. Get user's known artists (from UserSongs)
2. Query BigQuery for songs:
   - By known artists (high weight)
   - By popular karaoke songs (fallback)
3. Score and rank
4. Add recommendation reason for transparency

### Filter/Sort Implementation

Add query parameters to existing `/api/catalog/songs` endpoint:
- `decade: str` - Filter by release decade
- `energy: str` - Filter by energy level (requires Spotify audio features join)
- `in_my_library: bool` - Only songs from user's listening history
- `min_popularity: int` - Minimum popularity threshold
- `sort: str` - Sort field (relevance, popularity, artist, title)
- `sort_dir: str` - asc/desc

## Implementation Steps

### Step 1: Data Models (1 file)

Add to `karaoke_decide/core/models.py`:
- `QuizSong` - Song presented in quiz
- `QuizResponse` - User's quiz submission
- Update `User` model with quiz fields

### Step 2: Quiz Service (1 new file)

Create `backend/services/quiz_service.py`:
- `get_quiz_songs(count: int) -> list[QuizSong]`
- `submit_quiz(user_id: str, response: QuizResponse) -> User`
- `get_user_quiz_status(user_id: str) -> QuizStatus`

### Step 3: Quiz Routes (1 new file)

Create `backend/api/routes/quiz.py`:
- `GET /api/quiz/songs` - Get quiz song options
- `POST /api/quiz/submit` - Submit quiz responses
- `GET /api/quiz/status` - Get user's quiz completion status

### Step 4: Recommendation Service (1 new file)

Create `backend/services/recommendation_service.py`:
- `get_recommendations(user_id: str, limit: int, filters: dict) -> list[Recommendation]`
- `_score_song(song: KaraokeSong, user_context: UserContext) -> float`
- `_get_recommendation_reason(song: KaraokeSong, user_context: UserContext) -> str`

### Step 5: Recommendation Routes (1 new file)

Create `backend/api/routes/recommendations.py`:
- `GET /api/my/songs` - User's songs from listening history
- `GET /api/my/recommendations` - Personalized recommendations

### Step 6: Enhanced Catalog Filters (modify existing)

Update `backend/api/routes/catalog.py`:
- Add decade, energy, in_my_library filters
- Add sort parameter support
- Update BigQuery queries

Update `karaoke_decide/services/bigquery_catalog.py`:
- Add filter methods
- Add sorting support

### Step 7: Dependency Injection (modify existing)

Update `backend/api/deps.py`:
- Add `get_quiz_service_dep()`
- Add `get_recommendation_service_dep()`
- Add type aliases

### Step 8: Register Routes (modify existing)

Update `backend/api/routes/__init__.py`:
- Include quiz router
- Include recommendations router

### Step 9: Tests

Create comprehensive tests:
- `backend/tests/test_quiz.py` - Quiz endpoint tests
- `backend/tests/test_recommendations.py` - Recommendation tests
- `tests/unit/test_quiz_service.py` - Quiz service unit tests
- `tests/unit/test_recommendation_service.py` - Recommendation unit tests

### Step 10: Documentation

- Update `docs/API.md` with new endpoints
- Update `docs/README.md` status
- Update `docs/PLAN.md` to mark Phase 4 complete

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `backend/services/quiz_service.py` | Quiz business logic |
| `backend/services/recommendation_service.py` | Recommendation algorithm |
| `backend/api/routes/quiz.py` | Quiz API endpoints |
| `backend/api/routes/recommendations.py` | Recommendation endpoints |
| `backend/tests/test_quiz.py` | Quiz route tests |
| `backend/tests/test_recommendations.py` | Recommendation route tests |
| `tests/unit/test_quiz_service.py` | Quiz service unit tests |
| `tests/unit/test_recommendation_service.py` | Recommendation unit tests |

### Modified Files
| File | Changes |
|------|---------|
| `karaoke_decide/core/models.py` | Add QuizSong, QuizResponse, update User |
| `backend/api/deps.py` | Add service dependencies |
| `backend/api/routes/__init__.py` | Register new routers |
| `backend/api/routes/catalog.py` | Add filter/sort params |
| `karaoke_decide/services/bigquery_catalog.py` | Add filter methods |
| `backend/tests/conftest.py` | Add quiz/recommendation fixtures |
| `docs/API.md` | Document new endpoints |
| `docs/README.md` | Update status |
| `docs/PLAN.md` | Mark phase complete |

## Testing Strategy

### Unit Tests (70%+ coverage)

1. **Quiz Service Tests**
   - `test_get_quiz_songs_returns_stratified_sample`
   - `test_get_quiz_songs_excludes_low_popularity`
   - `test_submit_quiz_creates_user_songs`
   - `test_submit_quiz_updates_user_profile`
   - `test_submit_quiz_validates_song_ids`

2. **Recommendation Service Tests**
   - `test_recommendations_prioritize_known_artists`
   - `test_recommendations_include_reason`
   - `test_recommendations_respect_filters`
   - `test_recommendations_handle_no_history`
   - `test_score_calculation_weights`

### Backend Tests (60%+ coverage)

1. **Quiz Endpoint Tests**
   - `test_get_quiz_songs_success`
   - `test_get_quiz_songs_requires_auth`
   - `test_submit_quiz_success`
   - `test_submit_quiz_invalid_song_ids`
   - `test_quiz_status_shows_completion`

2. **Recommendation Endpoint Tests**
   - `test_get_recommendations_success`
   - `test_get_recommendations_with_filters`
   - `test_get_recommendations_empty_for_new_user`
   - `test_my_songs_returns_user_library`

3. **Catalog Filter Tests**
   - `test_filter_by_decade`
   - `test_filter_by_energy`
   - `test_sort_by_popularity`
   - `test_combined_filters`

### Integration Test Scenarios

1. **New User Quiz Flow**
   - Request magic link → Verify → Get quiz → Submit → Get recommendations

2. **Existing User with History**
   - Connect Spotify → Sync → Get recommendations → Verify artist matches

## API Endpoint Details

### Quiz Endpoints

```
GET /api/quiz/songs
  Response: {
    "songs": [
      {
        "id": "queen-bohemian-rhapsody",
        "artist": "Queen",
        "title": "Bohemian Rhapsody",
        "decade": "1970s",
        "popularity": 85
      }
    ]
  }

POST /api/quiz/submit
  Request: {
    "known_song_ids": ["queen-bohemian-rhapsody", "journey-dont-stop-believin"],
    "decade_preference": "1980s",
    "energy_preference": "high"
  }
  Response: {
    "message": "Quiz completed successfully",
    "songs_added": 2,
    "recommendations_ready": true
  }

GET /api/quiz/status
  Response: {
    "completed": true,
    "completed_at": "2024-12-30T12:00:00Z",
    "songs_known_count": 5
  }
```

### Recommendation Endpoints

```
GET /api/my/songs?page=1&per_page=20
  Response: {
    "songs": [...],
    "total": 150,
    "page": 1,
    "per_page": 20,
    "has_more": true
  }

GET /api/my/recommendations?limit=20&decade=1980s&energy=high
  Response: {
    "recommendations": [
      {
        "song": {
          "id": "bon-jovi-livin-on-a-prayer",
          "artist": "Bon Jovi",
          "title": "Livin' on a Prayer"
        },
        "score": 0.87,
        "reason": "You listen to Bon Jovi"
      }
    ]
  }
```

### Enhanced Catalog Filters

```
GET /api/catalog/songs?q=rock&decade=1980s&energy=high&sort=popularity&sort_dir=desc
```

## Open Questions

1. **Quiz Song Caching Strategy**
   - How often to refresh quiz song candidates?
   - Store in Firestore or in-memory cache?
   - Recommendation: Refresh daily, cache in Firestore

2. **Energy Level Mapping**
   - Need to join Spotify audio features for energy filtering
   - This is a larger ETL task - should we defer?
   - Recommendation: Start with decade filter only, add energy in Phase 4.5

3. **Cold Start for Brand New Users**
   - What to show users who skip quiz and have no history?
   - Recommendation: Show "crowd pleasers" (high brand count + popularity)

4. **Recommendation Caching**
   - Cache recommendations per user?
   - Invalidate on sync or quiz update?
   - Recommendation: Cache for 1 hour, invalidate on data changes

## Dependencies

- Phase 3 complete (Music Service Integration) ✅
- BigQuery catalog with popularity data ✅
- Firestore for user data storage ✅
- UserSong model and storage ✅

## Estimated Scope

- **New code**: ~800-1000 lines
- **Test code**: ~600-800 lines
- **Files changed**: 15-18 files
- **Risk level**: Medium (new algorithm, needs tuning)

## Success Criteria

1. User without streaming data can complete quiz and get recommendations
2. User with streaming history sees recommendations based on their artists
3. All tests pass with required coverage
4. API documentation is complete
5. Response times under 500ms
