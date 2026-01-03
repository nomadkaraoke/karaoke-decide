# User Data Vision & Requirements

> Captured from product owner feedback on 2026-01-02. This document defines the vision for how we collect, store, and display user music data.

## Core Philosophy

The "My Data" page serves two purposes:
1. **Transparency** - Show users exactly what data we have about their music taste
2. **Confidence** - Help the product team verify we're fetching the right data from sources

The goal is to collect as much user-specific listening data as possible to power personalized karaoke recommendations.

## Data Sources & What to Fetch

### Spotify (Limited Data)

**Artists:**
- Top artists from 3 time ranges (short_term, medium_term, long_term)
- Each time range returns up to 50 artists ranked by user affinity
- Total: ~150 artist entries (with overlap across time ranges)
- Store: rank, time_range, popularity (for display only)

**Songs:**
- Top tracks from 3 time ranges (short_term, medium_term, long_term)
- Each time range returns up to 50 tracks ranked by user affinity
- Total: ~150 track entries (with overlap)
- Store: rank, time_range, popularity, duration

**Limitation:** Spotify's API only provides "top" items, not full listening history.

### Last.fm (Rich Data)

**Artists:**
- Top artists endpoint with pagination
- Can fetch up to 1,000+ artists ranked by playcount
- Store: rank, playcount (actual listen count), period

**Songs (Scrobbles):**
- **Critical:** Last.fm stores EVERY song a user has ever listened to
- Users can have 100,000+ scrobbles spanning 15+ years
- We should fetch the complete scrobble history
- Store: artist, track, playcount, first_listened, last_listened

**Example:** Product owner has 219,625 scrobbles since 2008 - this is the gold standard for user music data.

**Implementation Note:** Fetching full scrobble history may take several minutes. This is acceptable for a sync job - prioritize completeness over speed.

## Data Model Requirements

### Principle: Preserve Source Data Separately

Store data from each source separately rather than merging:
- Allows showing "where we got this from"
- Preserves source-specific metadata (Spotify popularity vs Last.fm playcount)
- Enables re-syncing from specific sources without losing other data

### Artist Storage

```
user_artists:
  - user_id
  - source: "spotify" | "lastfm" | "quiz" | "manual"
  - artist_name
  - rank (position in user's top list)
  - playcount (Last.fm only - actual listen count)
  - popularity (Spotify only - global popularity 0-100)
  - time_range/period
  - updated_at
```

### Song/Track Storage

```
user_tracks:
  - user_id
  - source: "spotify" | "lastfm" | "manual"
  - artist
  - title
  - rank (for Spotify top tracks)
  - playcount (Last.fm - actual listen count)
  - first_listened (Last.fm)
  - last_listened (Last.fm)
  - spotify_popularity (for display)
  - matched_karaoke_song_id (if exists in catalog)
  - updated_at
```

## Display Requirements for My Data Page

### Section Order
1. **Preferences** (first - most actionable)
2. **Artists You Know**
3. **Songs You Know**
4. **Connected Services** (collapsed by default)

### Artists You Know Section

- Single unified list combining all sources
- Ranked by "how well user knows them":
  - Last.fm: Sort by playcount (actual listens)
  - Spotify: Sort by rank within time range
  - Manual/Quiz: Show at end (no ranking data)
- Show source badges (Spotify logo, Last.fm logo)
- Show additional info as pills:
  - Last.fm playcount (e.g., "1,234 plays")
  - Spotify popularity (e.g., "Pop: 87")
- These pills are informational only, not used for ranking

### Songs You Know Section

- Single unified list combining all sources
- Ranked by "how well user knows them":
  - Last.fm: Sort by playcount
  - Spotify: Sort by rank
- Show source badge
- Show playcount if available
- Indicate if karaoke version exists in catalog

### Connected Services Section

- Collapsed by default
- Collapsed state shows:
  - Service logos for connected services
  - Brief status (e.g., "Spotify, Last.fm connected")
- Expanded state shows:
  - Full connection details
  - Sync status and last sync time
  - Connect/disconnect buttons

## Future: Songs You Enjoy Singing

### Conceptual Distinction

There are two different concepts:
1. **Songs You Know** - Songs you've listened to (from sync data)
2. **Songs You Enjoy Singing** - Songs you've actually sung and rated

### Post-Song Survey (Future Feature)

From original KaraokeHunt roadmap - help users track what they've sung:

**Data to capture:**
- Which songs they've actually sung
- Whether they enjoyed singing it (thumbs up/down or 1-5 rating)
- Why they did/didn't enjoy it:
  - Outside my vocal range
  - Too difficult technically
  - Crowd didn't respond well
  - Awkward instrumental sections
  - Too long / boring parts
  - Perfect for me!

**Value:**
- Build personal "safe songs" list over time
- Inform recommendations (avoid similar issues)
- Remember what worked at past karaoke sessions

### Two User Archetypes

**Type A: Rich Data User (has Last.fm)**
- Already has comprehensive listening history
- Primary need: Track singing experiences over time
- Recommendations can start strong immediately

**Type B: Limited Data User (Spotify only or nothing)**
- Limited automatic data (max 150 tracks from Spotify)
- Primary need: Easy manual entry of artists/songs they like
- Quiz helps bootstrap preferences
- Needs more manual curation to get good recommendations

### Integration Strategy

The "Known Songs" manual entry feature should integrate with "Songs You Know":
- Known Songs = manually added songs user knows they like
- Should appear in "Songs You Know" list with "manual" source badge
- Can be enhanced with singing experience ratings

## Technical Requirements

### Sync Job Enhancements

1. **Last.fm Full History Sync**
   - Fetch ALL scrobbles, not just top tracks
   - Use pagination to handle 100k+ records
   - Show progress during long sync (may take 5-10 minutes)
   - Store individual track listen counts

2. **Spotify Top Items Sync**
   - Fetch top artists AND top tracks (currently may only do artists?)
   - All 3 time ranges
   - Store with rank information

3. **Data Freshness**
   - Last.fm: Full re-sync periodically, incremental for new scrobbles
   - Spotify: Full re-sync (data changes over time)

### API Verification Needed

- [ ] Verify Last.fm API can return full scrobble history
- [ ] Verify pagination limits for Last.fm top artists (1000+?)
- [ ] Verify Spotify top tracks endpoint is being called
- [ ] Verify data is being stored correctly per source

## Success Metrics

1. **Data Completeness:**
   - Last.fm users should see their full scrobble count reflected
   - Spotify users should see ~150 artists and ~150 tracks

2. **User Understanding:**
   - Users can see exactly what data informed their recommendations
   - Users can identify gaps and add manual data to improve

3. **Recommendation Quality:**
   - More data = better recommendations
   - Users with Last.fm should get noticeably better recommendations

## References

- [Original KaraokeHunt Roadmap](archive/2023-02-original-karaokehunt-roadmap.md) - Post-song survey concept
- [My Data Page Archive](archive/2026-01-02-my-data-page.md) - Current implementation
