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

1. **What you know** - Recommend songs from artists you listen to (via Spotify/Last.fm top artists, or quiz selections)
2. **What you can sing** - Optional vocal range detection matches you to singable songs
3. **What's popular** - Filter by karaoke crowd-pleasers and general popularity
4. **Your preferences** - Quick quiz captures genres, decades, and energy preferences

### Key Insight: Any Song Can Be Karaoke

With [Nomad Karaoke Generator](https://gen.nomadkaraoke.com), we can create a karaoke version of *any song* in under 10 minutes. This means:

- Recommendations aren't limited to existing karaoke catalogs
- If a perfect song doesn't have a karaoke version, we link to one-click generation
- The focus shifts from "what's available" to "what would be great for YOU to sing"

## UX Philosophy

### Navigation Design

The app uses action-oriented, user-focused navigation:

| Tab | Purpose | Rationale |
|-----|---------|-----------|
| **Recommendations** | Core value - personalized song suggestions | This is why users come; make it the primary destination |
| **Music I Know** | All music data in one place | Consolidates artists, songs, and service connections |
| **Playlists** | Saved karaoke lists | Quick access to curated sets |
| **Settings** | Profile and preferences | Standard location for account management |

### Key Principles

1. **Get to value fast** - Authenticated users skip the landing page and go straight to recommendations
2. **Streamlined onboarding** - Quiz is 3 steps max (Genres → Preferences → Artists), with skip options
3. **Transparency over magic** - "Music I Know" shows exactly what data influences recommendations
4. **Action-oriented naming** - "Recommendations" not "Discover", "Music I Know" not "My Data"

### Flow Philosophy

- **Landing page** exists only for first-time/logged-out users - explains value prop, single CTA
- **Quiz** captures enough to make recommendations useful, then gets out of the way
- **Recommendations** is the home base - where users return to find their next song
- **Settings** consolidates profile, preferences, and account actions in one place

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

5. **User Music Preferences** - See detailed breakdown in "Music Service Data Capabilities" section below

## Vocal Range Feature

### Phased Rollout Strategy

Vocal range matching is complex and expensive to do well. We're taking a phased approach that builds data incrementally while delivering value at each stage.

#### Phase 1: Crowd-Source Singability Data (Free)

**Goal:** Build a database of song singability ratings from real karaoke performances.

**How it works:**
1. After a user sings a song (or marks it as sung), prompt: *"How comfortable was this to sing?"*
2. Collect ratings: Easy / Just Right / Challenging / Too Hard
3. Aggregate across users to build singability profiles per song
4. Factor in user demographics if available (age, gender as vocal range proxy)

**Benefits:**
- Zero cost to implement (just UI + storage)
- Real-world data beats theoretical analysis
- Builds community engagement
- Works immediately with existing catalog

**Data model:**
```
song_singability_ratings
├── song_id
├── user_id
├── rating: "easy" | "just_right" | "challenging" | "too_hard"
├── user_vocal_type (optional, self-reported)
└── created_at
```

#### Phase 2: Personalized Vocal Analysis (Paid Feature)

**Goal:** Let serious karaoke fans get personalized singability predictions.

**How it works:**
1. User selects up to **1,000 songs they know well** from their library
2. User records a short vocal sample (60 seconds singing a scale or known song)
3. We analyze their vocal range from the recording
4. We cross-reference their known songs with our crowd-sourced singability data
5. For songs with audio analysis data, we calculate a personalized singability score

**Pricing consideration:** This is compute-intensive and provides high value. Could be:
- One-time analysis fee ($5-10)
- Part of a premium subscription
- Free for first 100 songs, paid for full 1,000

**Why this approach works:**
- Users who've sung 1,000+ songs at karaoke are serious about it (willing to pay)
- Their personal history tells us more than generic vocal type labels
- Combines crowd wisdom with individual calibration

### Technical Implementation

#### User Vocal Detection
- Optional enhancement, not required for onboarding
- "Sing into your phone for 60 seconds" (scale or free singing)
- Works in browser using Web Audio API
- Returns: "You're likely a Baritone (C2-G4)"

#### Technical Approach (Research Needed)
1. **Research existing APIs first** - Cyanite.ai, other music analysis services
2. **If we build it ourselves:**
   - Pitch detection using Web Audio API or server-side with librosa
   - Map detected range to voice type classifications
   - Only show classification if confidence is high
   - Be honest: "We're not 100% sure, but based on your recording..."

#### Song Matching
- Store vocal range data per song (min/max pitch, tessitura)
- Match user range to song range with configurable tolerance
- "Show me songs within 2 semitones of my range"
- Combine with crowd-sourced singability ratings for best results

## Music Service Data Capabilities

Understanding exactly what data we can extract from each music service is critical for setting user expectations and building honest features.

### Spotify API

**Scopes required:** `user-top-read user-library-read`

#### Artist-Level Data
| Data Type | Availability | Details |
|-----------|--------------|---------|
| Top Artists | ~150 max | 50 per time range (short_term, medium_term, long_term), deduplicated |
| Followed Artists | Available | Requires `user-follow-read` scope (not currently enabled) |

#### Song-Level Data
| Data Type | Availability | Details |
|-----------|--------------|---------|
| Top Tracks | ~150 max | 50 per time range × 3 ranges, deduplicated |
| Saved/Liked Tracks | Thousands | Paginated via `/me/tracks`, can fetch user's entire liked library |
| Full Listening History | **NOT AVAILABLE** | Spotify does not expose play history beyond top tracks |

**Key limitation:** Spotify's API does not provide full listening history. We can only see:
1. What tracks are in the user's top 50 (per time range)
2. What tracks the user has explicitly "liked" (saved)

**Recommendation engine implications:**
- Use top artists heavily (good signal of taste)
- Saved tracks are high-quality signal (user explicitly liked them)
- Cannot see "songs they've listened to but didn't save"

### Last.fm API

**Authentication:** Username only (public scrobble data)

#### Artist-Level Data
| Data Type | Availability | Details |
|-----------|--------------|---------|
| Top Artists | Up to 1,000 | Single request with `limit=1000` via `user.getTopArtists` |
| Artist Play Counts | Yes | Total scrobbles per artist |

#### Song-Level Data
| Data Type | Availability | Details |
|-----------|--------------|---------|
| Top Tracks | 10,000+ | Paginated via `user.getTopTracks`, 1000/page |
| Track Play Counts | Yes | Exact play count per track |
| Full Scrobble History | **YES** | `user.getRecentTracks` returns every scrobble with timestamp |
| Loved Tracks | Yes | Tracks user explicitly marked as loved |

**Key advantage:** Last.fm is the only service that provides full song-level listening history. For users with Last.fm connected, we have:
- Every song they've ever scrobbled (with play counts)
- Timestamp data (when they listened)
- Ability to import their complete listening history into our database

**Recommendation engine implications:**
- Last.fm users get the richest experience
- Can recommend based on actual songs listened, not just artists
- Play counts indicate familiarity (songs listened 10+ times = knows the lyrics)
- Should encourage Spotify users to also connect Last.fm for better recommendations

### Apple Music (Future)

Not yet implemented. API research needed.

### Quiz-Based Preferences

For users without streaming data or who prefer not to connect services:

#### Artist-Level Data
| Data Type | Source | Details |
|-----------|--------|---------|
| Liked Artists | Quiz selection | User picks artists they know from curated list |
| Preferred Genres | Quiz selection | 15 inclusive genre categories |

#### Song-Level Data
| Data Type | Source | Details |
|-----------|--------|---------|
| Known Songs | Future quiz | "Pick songs you know" from popular karaoke list |
| Loved/Hated Songs | Manual feedback | User marks songs after seeing recommendations |

### Data Storage Strategy

Given these API limitations, we should store:

```
User Preferences (Firestore)
├── artists_liked[]           # From any source, with source tag
│   ├── artist_name
│   ├── source: "spotify" | "lastfm" | "quiz" | "manual"
│   └── play_count (if available)
├── songs_liked[]             # High-confidence songs
│   ├── track_id (spotify/internal)
│   ├── artist, title
│   ├── source
│   └── play_count (if available)
├── songs_loved[]             # Explicit positive signal
├── songs_hated[]             # Explicit negative signal (never recommend)
├── genres_preferred[]
├── decades_preferred[]
└── connected_services{}
    ├── spotify: { connected_at, last_sync }
    └── lastfm: { username, connected_at, last_sync }
```

### Recommendation Engine Weighting

Different data sources should have different weights:

| Data Source | Artist Weight | Song Weight | Rationale |
|-------------|---------------|-------------|-----------|
| Last.fm scrobbles (10+ plays) | N/A | 1.0 | User definitely knows this song |
| Last.fm scrobbles (3-9 plays) | N/A | 0.7 | User probably knows this song |
| Spotify saved tracks | N/A | 0.9 | User explicitly liked it |
| Spotify top tracks | N/A | 0.8 | High engagement |
| Spotify/Last.fm top artists | 1.0 | N/A | Strong taste signal |
| Quiz-selected artists | 0.8 | N/A | User says they like them |
| User-loved songs | N/A | 1.0 | Explicit positive signal |
| User-hated songs | N/A | -1.0 | Never recommend |

## User Data & Profile ("My Data")

### Design Decision: Transparency Over "Library" Illusion

**Problem:** The original "My Songs" / "Songs in Your Library" feature was misleading because:

1. **We don't actually have song-level listening data for most users.** Spotify's API provides top artists and top tracks (limited), but not full listening history. Only Last.fm provides comprehensive song-level scrobble data, and very few users have Last.fm accounts.

2. **The quiz added confusion.** When users selected artists during the quiz, those artists' songs were added to "My Songs" - making it feel like recommendations rather than their actual library. The mental model was broken.

3. **"Music Services" was too narrow.** Users had no visibility into what data the system actually knew about them or how that data influenced recommendations.

### Solution: "My Data" Tab

Replace both "My Songs" and "Music Services" with a unified **"My Data"** tab that shows users all inputs to the recommendation engine.

#### Section 1: Connected Services
Show which services are connected and **what data each provides**:

| Service | Status | What We Get |
|---------|--------|-------------|
| Spotify | Connected ✓ | 127 top artists, 89 saved tracks |
| Last.fm | Not connected | [Connect to import full listening history] |

Include a prompt encouraging Spotify-only users to connect Last.fm for richer song-level data.

#### Section 2: Your Artists (Artist-Level Data)
Editable list of artists you like, with source attribution:

| Artist | Source | Actions |
|--------|--------|---------|
| Taylor Swift | Spotify top artist | [Remove] |
| Green Day | Quiz selection | [Remove] |
| Paramore | Added manually | [Remove] |

[+ Add Artist] button for manual additions.

#### Section 3: Your Songs (Song-Level Data)
Only shown if user has song-level data (Last.fm or Spotify saved tracks):

| Song | Artist | Source | Play Count | Actions |
|------|--------|--------|------------|---------|
| Mr. Brightside | The Killers | Last.fm (47 plays) | 47 | [Love] [Hide] |
| Bohemian Rhapsody | Queen | Spotify saved | - | [Love] [Hide] |

#### Section 4: Preferences
Quiz-derived preferences, all editable:

- **Genres:** Rock, Pop, Indie [Edit]
- **Decades:** 90s, 2000s [Edit]
- **Energy:** Mix of chill and high energy [Edit]

#### Section 5: Feedback (Future)
- **Loved songs:** Songs explicitly marked as great to sing
- **Hidden songs:** Songs you never want recommended
- **Vocal range:** Your detected range (once implemented)
- **Karaoke history:** Songs sung and how they went

### Benefits

1. **Transparency:** Users understand exactly what the system knows about them
2. **Experimentation:** Easy to tweak preferences and see how recommendations change (e.g., add/remove genres, change decade preferences)
3. **Control:** Clear path to modify any data point
4. **Honest mental model:** No pretending we have data we don't have

### Implementation Notes

- All data should be editable directly from "My Data"
- Show data source for each item (e.g., "from Spotify" vs "from quiz")
- Group by category: Preferences, Connected Services, Feedback, etc.
- Consider showing "recommendation weight" for power users

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
- **Vocal range:** Phased approach - crowd-source singability first, then paid personalized analysis
- **Data-light approach:** Quiz-based onboarding
- **Platform:** CLI + API first, then responsive web
- **UX philosophy:** Action-oriented navigation, streamlined 3-step quiz, get users to recommendations fast
- **MLP excludes:** Vocal range, post-song tracking, social features, venue filtering
- **User data transparency:** "Music I Know" consolidates all music data with source attribution
