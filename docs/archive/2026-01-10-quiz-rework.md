# Quiz Onboarding Rework - 2026-01-10

## Summary

Complete rework of the quiz onboarding flow to gather richer user preferences and provide smarter artist recommendations. The quiz now has 5 steps (up from 3), expanded genre selection, multi-decade preferences, and uses user input to intelligently suggest artists they might know.

## Key Changes

### Quiz Flow (5 steps)

| Step | Name | Features |
|------|------|----------|
| 1 | **Genres** | 22 genres (7 new: punk, emo, grunge, folk, blues, ska, other) |
| 2 | **Favorite Eras** | Multi-select decades (added 1950s, 1960s) |
| 3 | **Quick Preferences** | Energy + vocal comfort + crowd pleaser preferences |
| 4 | **Music You Know** | Manual artist entry + songs you enjoy singing with metadata |
| 5 | **Artists You Know** | Smart artist selection informed by previous steps |

### New Genre Options

Added 7 genres to address gaps (e.g., emo/goth, punk were missing):
- `punk` - Green Day, Blink-182, The Offspring
- `emo` - My Chemical Romance, Fall Out Boy, Paramore
- `grunge` - Nirvana, Pearl Jam, Soundgarden
- `folk` - Mumford & Sons, The Lumineers, Hozier
- `blues` - B.B. King, Eric Clapton, John Mayer
- `ska` - No Doubt, Sublime, Reel Big Fish
- `other` - Catch-all for users who don't see their taste

### New Preference Options

**Vocal Comfort:** Helps with primitive vocal range filtering
- `easy` - Prefer songs in comfortable vocal range
- `challenging` - Love vocal challenges
- `any` - No preference

**Crowd Pleaser:** Song discovery style
- `hits` - Popular songs everyone knows
- `deep_cuts` - Hidden gems and B-sides
- `any` - Mix of both

### Smart Artist Selection

New `POST /api/quiz/artists/smart` endpoint that:
1. Takes user's genre selections from step 1
2. Takes decade preferences from step 2
3. Infers additional genres from manually entered artists (step 4)
4. Infers genres from songs user enjoys singing (step 4)
5. Returns artists matching the combined criteria

### Recommendation Engine Updates

- Multi-decade matching (was single decade)
- Vocal comfort bonus: Boosts songs by artists from songs user marked as comfortable
- Crowd pleaser adjustment: Adjusts popularity weighting based on user preference
- New `comfortable_artist_keys` set for primitive vocal range filtering

## Files Changed

- `karaoke_decide/core/models.py` - User model fields for new preferences
- `backend/api/routes/quiz.py` - New endpoint, updated request models
- `backend/services/quiz_service.py` - Smart artist selection logic
- `backend/services/recommendation_service.py` - Enhanced scoring
- `frontend/src/app/quiz/page.tsx` - Complete 5-step quiz UI
- `frontend/src/lib/api.ts` - API client methods

## Decisions Made

1. **"Other/Not Sure" genre**: Implemented as a catch-all that disables genre filtering when selected, rather than mapping to specific genres.

2. **Backward compatibility**: Legacy `quiz_decade_pref` (single) preserved alongside new `quiz_decades` (list) for existing users.

3. **Primitive vocal range**: Instead of complex vocal analysis, we track artists from songs marked as "comfortable" and boost their other songs in recommendations. Same artist = similar vocal range assumption.

4. **Smart artist ordering**: Artists informed by manual entries appear first in step 5, making it more likely users will find artists they know.

## Future Considerations

- Could add energy/vocal_comfort/genre filters to recommendations page (backend filtering)
- Audio features data could enable true tempo/energy filtering
- Social features could use "songs I enjoy singing" data for duet/group suggestions
