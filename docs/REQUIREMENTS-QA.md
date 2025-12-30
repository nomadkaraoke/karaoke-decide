# Requirements Q&A Session

*December 2024 - Clarifying the vision for Nomad Karaoke Decide*

This document captures the Q&A that shaped the project vision.

---

## Context

The original KaraokeHunt roadmap was from February 2023. Key changes since then:

1. **Karaoke-gen exists** - We can now generate karaoke for any song in ~10 minutes, so recommendations aren't limited to existing karaoke versions
2. **Vocal range excites people** - When talking to potential users, helping them find songs they can actually sing gets them excited
3. **Need to serve everyone** - Both data-rich power users AND casual "going to karaoke drunk" users

---

## Core Value Proposition

### Q1: Target Users
**Who's the primary user you want to nail first?**

**Answer: C) Both equally from day one**

### Q2: The "Decide" Problem
**What's the actual problem you're solving?**

**Answer:** All of these, potentially via different modes in the web frontend:
- "I don't know what I want to sing" (discovery problem)
- "I know songs I like but don't know if I can sing them" (capability matching)
- "I'm overwhelmed by the karaoke book" (filtering/narrowing)
- "I want to find something that will be fun for the crowd" (social/performance)

Discovery is the core goal first - helping people find songs they might want to sing from artists they know, or helping people choose a crowdpleaser for their particular group of friends.

### Q3: Karaoke-Gen Integration
**How tightly coupled should this be with karaoke-gen?**

**Answer: A) Standalone product that can optionally trigger karaoke-gen**

Decide should help people find songs to sing. If there's already a good karaoke version on YouTube, just link them out to it. If not, give them a one-click button to make it with the Nomad Karaoke Generator.

---

## Vocal Range Feature

### Q4: Vocal Range Priority
**Should vocal range detection be a core onboarding step, optional enhancement, or key differentiator?**

**Answer: B & C - Optional enhancement AND key differentiator**

It's definitely gotta be an optional feature because not everyone wants to sing into their phone (or are even able to, depending where they are). But if we can implement it in a way where it really works well - e.g., average person can sing into the app for a minute and get meaningful karaoke song recommendations based on that - then yeah we should definitely market it as a key feature because that's awesome!

### Q5: Vocal Range Implementation
**How precise do you want vocal range detection?**

**Answer: B) Moderate - detect actual notes (e.g., "You're a baritone, C2-G4")**

It would definitely be nice to be able to tell someone reliably if they're probably a baritone, soprano, etc. - but only if we can actually figure out a technical solution that works well, reliably and accurately. I don't wanna be fooling people if the tech really doesn't have a clue.

### Q6: Song Vocal Data
**Where does vocal range data for songs come from?**

**Answer:** Research existing APIs first (e.g., Cyanite.ai). If we have to build our own:

- **flacfetch** - CLI/library/cloud API to download pretty much any song on demand
- **audio-separator** - Python library and Modal-hosted GPU service to separate vocals
- Can generate our own vocal analysis on demand (async, few minutes per song)
- Pre-analyze top 1000 most popular karaoke songs
- Expand database over time as users trigger more analyses

---

## Data-Light Users

### Q7: Minimum Viable Input
**For a user with NO data connections, what's the minimum we need to give them useful recommendations?**

**Answer: C) Answer a quiz**

Options like "pick songs you know" (from a list of popular karaoke songs), "pick your fave/least fave from this list", decade preferences, etc.

Similar to OKCupid - can keep answering more questions to improve filtering, but default onboarding should be fairly quick with maximum of a few questions, whichever are most valuable for shaping recommendations.

Still offer power features (vocal range detection, music streaming connection) prominently so power users can find them easily and skip the quiz.

### Q8: Popularity Data
**What popularity data do you have access to?**

**Answer:**

1. **KaraokeNerds catalog** - Available at `gs://projectbread-karaokay.appspot.com/karaokenerds-data/full/full-data-latest.json.gz` (synced daily). Doesn't have explicit popularity but can use brand coverage as a signal (e.g., Bohemian Rhapsody covered by 45 different brands)

2. **Spotify metadata archive** (July 2025) from Anna's Archive - Very rich, accurate and complete popularity data for all songs on Spotify as of 2025. Location: `/Volumes/AndrewMacSD/spotify-metadata-dump/annas_archive_spotify_2025_07_metadata/`

---

## Features to Revisit

### Q9: Music Service Integration (Spotify/Last.fm)
**Still want this?**

**Answer:** Definitely still want this, just as an optional feature. "I myself want to use this app, and I definitely want it to be enriched with any data we can get from both my own Spotify and Last.fm."

### Q10: Song Analysis Data
**Still want duration, BPM, energy, key, mood, danceability, etc.?**

**Answer:** Yes, use whatever data we have from the Spotify dump. Trust Claude's judgment on what makes sense to add as filters once the data is explored.

### Q11: Post-Song Survey / Tracking
**Is tracking singing history still important for MLP?**

**Answer:** Great idea but future feature. Document on roadmap but not in MLP.

### Q12: Social Features
**Friend filter, group playlists, karaoke crews - priority for MLP?**

**Answer:** Document on roadmap but not in MLP.

### Q13: Karaoke Bar Filter
**Still relevant given karaoke-gen exists?**

**Answer:** Still relevant (many bars don't use YouTube) but not a key feature initially. Document on roadmap but not in MLP.

---

## Platform & UX

### Q14: Platform Priority
**Confirm: CLI + API first, web later?**

**Answer: Yes**

Focus on building a really solid, well-tested, feature-rich cloud backend and CLI first. Then proceed with responsive, easy to use, beautiful frontend at decide.nomadkaraoke.com.

### Q15: Offline/At-Venue Use
**How important is offline support?**

**Answer:** Not necessary for MLP. Eventually after web frontend works well, revisit mobile app:
- Start from scratch, fully replace old FlutterFlow app
- More maintainable, not dependent on $70/month FlutterFlow subscription
- Will have offline mode after initial data sync
- Can plan that out in more detail later
