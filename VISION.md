# Nomad Karaoke Decide - Vision & Goals

> **Help anyone find the perfect song to sing at karaoke.**

## The Problem

Choosing what to sing at karaoke is surprisingly hard:

1. **"I don't know what I want to sing"** - The karaoke book has thousands of songs but nothing jumps out
2. **"Can I actually sing this?"** - You love a song but have no idea if it's in your range
3. **"What will the crowd enjoy?"** - You want something that gets people engaged, not awkward silence
4. **"I don't recognize half these songs"** - The catalog is full of songs you've never heard

Current solutions are inadequate:
- Karaoke apps just list songs with basic search
- Music streaming data isn't connected to karaoke
- No tools help match your voice to songs you can actually sing

## The Solution

**Nomad Karaoke Decide** helps you discover songs to sing based on:

1. **What you know** - Connect Spotify/Last.fm to see songs from your listening history
2. **What you can sing** - Optional vocal range detection matches you to singable songs
3. **What's popular** - Filter by karaoke crowd-pleasers and general popularity
4. **Your preferences** - Quick quiz for users without streaming data

### Key Insight: Any Song Can Be Karaoke

With [Nomad Karaoke Generator](https://gen.nomadkaraoke.com), we can create a karaoke version of *any song* in under 10 minutes. This means:

- Recommendations aren't limited to existing karaoke catalogs
- If a perfect song doesn't have a karaoke version, we link to one-click generation
- The focus shifts from "what's available" to "what would be great for YOU to sing"

## Target Users

We serve two user types **equally from day one**:

### Data-Rich Users
- Have Spotify, Last.fm, or Apple Music
- Want to find karaoke songs from artists they already know and love
- May want advanced features like vocal range matching
- Power users who track their karaoke performances

### Casual Users
- "I just want to find something fun to sing"
- Don't have streaming data or don't want to connect it
- Going to karaoke tonight and need ideas NOW
- Want quick recommendations based on simple preferences

## Core Value Propositions

### 1. Discovery Mode (Primary)
*"Help me find songs I might want to sing"*

- Show songs from artists you know (via streaming history)
- Surface karaoke crowd-pleasers you recognize
- Quick quiz fallback for data-light users
- Filter by decade, genre, tempo, mood, energy

### 2. Singability Mode
*"Show me songs I can actually sing"*

- Optional vocal range detection (sing into the app for 60 seconds)
- Match user's range to songs within their capability
- Classify songs by vocal difficulty
- Tell users "You're a baritone (C2-G4)" if the tech is reliable

### 3. Crowd-Pleaser Mode
*"Help me pick something the crowd will love"*

- Filter by karaoke popularity (songs covered by many brands)
- Factor in general music popularity (Spotify plays)
- Surface "safe choices" that always work
- Consider the social context of the performance

### 4. Personal History Mode (Future)
*"What worked for me before?"*

- Track songs you've sung and how they went
- Build a personal "go-to" list over time
- Learn from your ratings and notes

## Integration with Karaoke Generator

**Loose coupling, seamless handoff:**

1. User finds a song recommendation
2. If karaoke version exists on YouTube → link directly
3. If no karaoke version exists → "Make it with Nomad Karaoke Generator" button
4. One-click handoff to gen.nomadkaraoke.com with artist/title pre-filled
5. Generator handles auth, credits, and job submission

## Data Sources

### Available Now:
1. **KaraokeNerds Catalog** - Existing karaoke songs with brand coverage as popularity signal
   - Location: `gs://projectbread-karaokay.appspot.com/karaokenerds-data/full/full-data-latest.json.gz`
   - Synced daily

2. **Spotify Metadata Archive** (July 2025)
   - 256M tracks with popularity scores (0-100)
   - Artist genres and follower counts
   - Album metadata and release dates
   - Location: `/Volumes/AndrewMacSD/spotify-metadata-dump/annas_archive_spotify_2025_07_metadata/`

3. **Spotify Audio Features** (July 2025)
   - BPM, key, danceability, energy, valence
   - Speechiness, acousticness, instrumentalness
   - Location: Same dump, `spotify_clean_audio_features.sqlite3.zst`

### To Build:
4. **Vocal Range Analysis** (for songs)
   - Use flacfetch to download audio on-demand
   - Use audio-separator to isolate vocals
   - Analyze vocal track for pitch range
   - Pre-compute top 1000 karaoke songs, expand over time

5. **User Listening History**
   - Real-time from Spotify API (with user OAuth)
   - Real-time from Last.fm API
   - Apple Music (future)

## Vocal Range Feature

### User Experience
- Optional enhancement, not required for onboarding
- "Sing into your phone for 60 seconds" (scale or free singing)
- Works in browser using Web Audio API
- Returns: "You're likely a Baritone (C2-G4)"

### Technical Approach (Research Needed)
1. **Research existing APIs first** - Cyanite.ai, other music analysis services
2. **If we build it ourselves:**
   - Pitch detection using Web Audio API or server-side with librosa
   - Map detected range to voice type classifications
   - Only show classification if confidence is high
   - Be honest: "We're not 100% sure, but based on your recording..."

### Song Matching
- Store vocal range data per song (min/max pitch, tessitura)
- Match user range to song range with configurable tolerance
- "Show me songs within 2 semitones of my range"

## Quiz-Based Onboarding (Data-Light Users)

For users without streaming data, a short quiz captures preferences:

### Quick Quiz (Default)
1. **Pick songs you know** - Show 10 popular karaoke songs, user selects which they recognize
2. **Decade preference** - 80s, 90s, 2000s, 2010s, 2020s, or "mix"
3. **Energy level** - Chill ballad vs. high energy banger

### Extended Quiz (Optional, OKCupid-style)
- Genre deep-dive
- Specific artists they love
- Songs they've sung before that went well
- Mood preferences

## Platform Strategy

### Phase 1: CLI + API
- Backend API on Cloud Run (GCP)
- CLI for power users and testing
- All core recommendation logic

### Phase 2: Web Frontend
- Responsive web app at decide.nomadkaraoke.com
- Works on mobile browsers
- Vocal range detection via Web Audio API

### Phase 3: Mobile App (Future)
- Native app replacing old FlutterFlow KaraokeHunt
- Offline support after initial sync
- Enhanced on-device capabilities

## MLP (Minimum Lovable Product) Scope

### In Scope:
- [ ] Magic link auth (email-based, no passwords)
- [ ] Spotify OAuth integration (optional)
- [ ] Last.fm integration (optional)
- [ ] Quiz-based onboarding for data-light users
- [ ] Song recommendations based on listening history
- [ ] Song search and browse with filters (decade, genre, popularity)
- [ ] KaraokeNerds catalog integration
- [ ] Spotify popularity and audio features data
- [ ] Basic playlist creation
- [ ] Link to YouTube karaoke (if exists) or Karaoke Generator (if not)

### Future Roadmap (Not MLP):
- [ ] Vocal range detection (user)
- [ ] Vocal range analysis (songs)
- [ ] Post-song survey and tracking
- [ ] Social features (friends, crews, group playlists)
- [ ] Karaoke bar filter (songs available at venue)
- [ ] Mobile app with offline mode
- [ ] Apple Music integration

## Success Metrics

### MLP Launch Criteria
- User can get song recommendations in <30 seconds
- Works for both data-rich and data-light users
- Recommendations feel relevant and useful
- Seamless handoff to Karaoke Generator works

### Key Metrics (Post-Launch)
- % of users who connect streaming services
- % of users who complete the quiz
- Click-through rate to YouTube karaoke or Generator
- User retention (return visits)
- Qualitative feedback: "Did this help you find a song?"

## Appendix: Answers to Vision Questions

See [docs/REQUIREMENTS-QA.md](docs/REQUIREMENTS-QA.md) for the full Q&A that shaped this vision.

### Key Decisions Summary:
- **Target users:** Both data-rich and casual, equally from day one
- **Core problem:** Discovery first, then singability and crowd-pleasing
- **Karaoke-gen integration:** Standalone with one-click handoff
- **Vocal range:** Optional but marketed if it works well
- **Data-light approach:** Quiz-based onboarding
- **Platform:** CLI + API first, then responsive web
- **MLP excludes:** Vocal range, post-song tracking, social features, venue filtering
