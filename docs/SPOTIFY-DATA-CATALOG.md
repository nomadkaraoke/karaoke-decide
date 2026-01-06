# Spotify Data Catalog

This document describes all Spotify data available in BigQuery for use in features and recommendations.

> **Last Updated:** 2026-01-06 (ETL in progress - 54% complete)
>
> **Location:** `nomadkaraoke.karaoke_decide.*`

## Data Sources

All data comes from the [Anna's Archive Spotify July 2025 dataset](https://annas-archive.org/datasets/spotify_2025_07):

1. **Metadata Torrent** (~200GB): Track, album, artist metadata + audio features
2. **Audio Analysis Torrent** (~3.88TB): Detailed audio analysis with sections, beats, bars, etc.

## Table Summary

| Table | Row Count | Description |
|-------|-----------|-------------|
| `spotify_track_artists` | 348M | Track-to-artist mapping (many-to-many) |
| `spotify_tracks` | 256M | Track metadata (title, artist, popularity) |
| `spotify_audio_features` | 230M | Audio features (danceability, energy, etc.) |
| `spotify_audio_analysis_sections` | 179M* | Song sections with timing and musical properties |
| `spotify_albums` | 59M | Album metadata |
| `spotify_audio_analysis_tracks` | 18M* | Track-level audio analysis summary |
| `spotify_artists` | 15M | Artist metadata |
| `spotify_artist_genres` | 2.2M | Artist-to-genre mapping (768 unique genres) |

*ETL in progress - counts will increase as more files are processed

---

## Metadata Tables

### spotify_tracks

Primary track information with denormalized artist data.

```sql
SELECT * FROM `nomadkaraoke.karaoke_decide.spotify_tracks` LIMIT 5
```

| Column | Type | Description |
|--------|------|-------------|
| `spotify_id` | STRING | Spotify track ID (e.g., "6dOtVTDdiauQNBQEDOtlAB") |
| `title` | STRING | Track title |
| `isrc` | STRING | International Standard Recording Code |
| `popularity` | INTEGER | 0-100 popularity score (higher = more popular) |
| `duration_ms` | INTEGER | Track duration in milliseconds |
| `explicit` | BOOLEAN | Whether track has explicit content |
| `artist_name` | STRING | Primary artist name (denormalized) |
| `artist_spotify_id` | STRING | Primary artist Spotify ID |
| `artist_popularity` | INTEGER | Primary artist popularity (0-100) |
| `artist_followers` | INTEGER | Primary artist follower count |

**Example Query - Find popular tracks:**
```sql
SELECT title, artist_name, popularity
FROM `nomadkaraoke.karaoke_decide.spotify_tracks`
WHERE popularity >= 80
ORDER BY popularity DESC
LIMIT 10
```

### spotify_albums

Album metadata including release information.

| Column | Type | Description |
|--------|------|-------------|
| `album_id` | STRING | Spotify album ID |
| `album_name` | STRING | Album title |
| `album_type` | STRING | "album", "single", "compilation" |
| `release_date` | STRING | Release date (YYYY, YYYY-MM, or YYYY-MM-DD) |
| `release_date_precision` | STRING | "year", "month", or "day" |
| `label` | STRING | Record label |
| `popularity` | INTEGER | Album popularity (0-100) |
| `total_tracks` | INTEGER | Number of tracks on album |

### spotify_artists

Artist metadata.

| Column | Type | Description |
|--------|------|-------------|
| `artist_id` | STRING | Spotify artist ID |
| `artist_name` | STRING | Artist name |
| `followers_total` | INTEGER | Total follower count |
| `popularity` | INTEGER | Artist popularity (0-100) |

### spotify_artist_genres

Maps artists to their genres. Artists can have multiple genres.

| Column | Type | Description |
|--------|------|-------------|
| `artist_id` | STRING | Spotify artist ID |
| `genre` | STRING | Genre name (768 unique genres) |

**Top 20 Genres by Track Count:**
| Genre | Track Count |
|-------|-------------|
| classical | 6.3M |
| classical piano | 3.8M |
| chamber music | 3.5M |
| opera | 3.0M |
| orchestra | 2.4M |
| jazz | 2.0M |
| lullaby | 1.7M |
| choral | 1.6M |
| requiem | 1.6M |
| big band | 1.3M |
| bhajan | 1.2M |
| trance | 1.1M |
| children's music | 1.1M |
| lo-fi | 1.1M |
| swing music | 1.0M |
| spoken word | 1.0M |
| christmas | 1.0M |
| progressive trance | 1.0M |
| minimal techno | 1.0M |
| reggae | 0.9M |

**Example Query - Find genres for an artist:**
```sql
SELECT g.genre
FROM `nomadkaraoke.karaoke_decide.spotify_artist_genres` g
JOIN `nomadkaraoke.karaoke_decide.spotify_artists` a ON g.artist_id = a.artist_id
WHERE a.artist_name = 'Taylor Swift'
```

### spotify_track_artists

Many-to-many mapping between tracks and artists (for collaborations).

| Column | Type | Description |
|--------|------|-------------|
| `track_id` | STRING | Spotify track ID |
| `artist_id` | STRING | Spotify artist ID |

---

## Audio Features Table

### spotify_audio_features

Spotify's audio analysis features for each track. All features are normalized 0-1 unless noted.

| Column | Type | Range | Description |
|--------|------|-------|-------------|
| `track_id` | STRING | - | Spotify track ID |
| `danceability` | FLOAT | 0-1 | How suitable for dancing (avg: 0.57) |
| `energy` | FLOAT | 0-1 | Intensity and activity (avg: 0.51) |
| `loudness` | FLOAT | dB | Overall loudness in decibels |
| `speechiness` | FLOAT | 0-1 | Presence of spoken words (avg: 0.14) |
| `acousticness` | FLOAT | 0-1 | Acoustic vs electronic (avg: 0.42) |
| `instrumentalness` | FLOAT | 0-1 | Predicts no vocals (avg: 0.32) |
| `liveness` | FLOAT | 0-1 | Presence of audience (avg: 0.20) |
| `valence` | FLOAT | 0-1 | Musical positivity/happiness (avg: 0.44) |
| `tempo` | FLOAT | BPM | Tempo in beats per minute (avg: 118) |
| `duration_ms` | INTEGER | ms | Track duration |
| `time_signature` | INTEGER | 3-7 | Time signature (beats per bar) |
| `key` | INTEGER | 0-11 | Musical key (0=C, 1=C#, 2=D, etc.) |
| `mode` | INTEGER | 0-1 | Major (1) or Minor (0) |

**Key Mapping:**
| Value | Key |
|-------|-----|
| 0 | C |
| 1 | C#/Db |
| 2 | D |
| 3 | D#/Eb |
| 4 | E |
| 5 | F |
| 6 | F#/Gb |
| 7 | G |
| 8 | G#/Ab |
| 9 | A |
| 10 | A#/Bb |
| 11 | B |

**Feature Descriptions:**

- **Danceability**: Based on tempo, rhythm stability, beat strength, regularity
- **Energy**: Based on dynamic range, loudness, timbre, onset rate, entropy
- **Valence**: Higher = happier/cheerful, Lower = sad/angry
- **Acousticness**: 1.0 = high confidence track is acoustic
- **Instrumentalness**: Values > 0.5 likely have no vocals
- **Liveness**: Values > 0.8 likely recorded live with audience
- **Speechiness**: > 0.66 = spoken word, 0.33-0.66 = music with speech, < 0.33 = music

**Example Query - Find upbeat dance tracks:**
```sql
SELECT t.title, t.artist_name, af.danceability, af.energy, af.valence, af.tempo
FROM `nomadkaraoke.karaoke_decide.spotify_tracks` t
JOIN `nomadkaraoke.karaoke_decide.spotify_audio_features` af ON t.spotify_id = af.track_id
WHERE af.danceability > 0.8
  AND af.energy > 0.7
  AND af.valence > 0.6
  AND t.popularity > 50
ORDER BY t.popularity DESC
LIMIT 20
```

---

## Audio Analysis Tables

### spotify_audio_analysis_tracks

Track-level summary from Spotify's audio analysis. Includes confidence scores for detected features.

| Column | Type | Description |
|--------|------|-------------|
| `spotify_id` | STRING | Spotify track ID |
| `duration` | FLOAT | Track duration in seconds |
| `tempo` | FLOAT | Overall tempo (BPM) |
| `tempo_confidence` | FLOAT | Confidence in tempo detection (0-1) |
| `time_signature` | INTEGER | Time signature (beats per bar) |
| `time_signature_confidence` | FLOAT | Confidence in time signature (0-1) |
| `key` | INTEGER | Musical key (0-11, see mapping above) |
| `key_confidence` | FLOAT | Confidence in key detection (0-1) |
| `mode` | INTEGER | Major (1) or Minor (0) |
| `mode_confidence` | FLOAT | Confidence in mode detection (0-1) |
| `loudness` | FLOAT | Overall loudness (dB) |
| `end_of_fade_in` | FLOAT | Time when fade-in ends (seconds) |
| `start_of_fade_out` | FLOAT | Time when fade-out starts (seconds) |
| `num_samples` | INTEGER | Number of audio samples |
| `analysis_sample_rate` | INTEGER | Sample rate used for analysis |
| `analyzer_version` | STRING | Version of Spotify's analyzer |
| `analysis_time` | FLOAT | Time taken to analyze (seconds) |

### spotify_audio_analysis_sections

Sections divide a track into distinct parts (verse, chorus, bridge, etc.). Each section has consistent musical properties.

| Column | Type | Description |
|--------|------|-------------|
| `spotify_id` | STRING | Spotify track ID |
| `section_index` | INTEGER | Section order (0-based) |
| `start` | FLOAT | Start time in seconds |
| `duration` | FLOAT | Section duration in seconds |
| `confidence` | FLOAT | Confidence in section boundary (0-1) |
| `loudness` | FLOAT | Section loudness (dB) |
| `tempo` | FLOAT | Section tempo (BPM) |
| `tempo_confidence` | FLOAT | Confidence in tempo (0-1) |
| `key` | INTEGER | Section key (0-11) |
| `key_confidence` | FLOAT | Confidence in key (0-1) |
| `mode` | INTEGER | Major (1) or Minor (0) |
| `mode_confidence` | FLOAT | Confidence in mode (0-1) |
| `time_signature` | INTEGER | Time signature |
| `time_signature_confidence` | FLOAT | Confidence in time signature (0-1) |

**Section Distribution:**
| Sections per Track | Count |
|-------------------|-------|
| 1-5 sections | 2.2M |
| 6-10 sections | 10.3M |
| 11-15 sections | 4.7M |
| 16-20 sections | 0.9M |
| 20+ sections | 0.4M |

**Example - Sections for "BIRDS OF A FEATHER" by Billie Eilish:**

| Section | Start | Duration | Loudness | Tempo | Key | Mode |
|---------|-------|----------|----------|-------|-----|------|
| 0 | 0.00s | 15.27s | -12.7 dB | 104.9 | D | Major |
| 1 | 15.27s | 21.72s | -11.4 dB | 105.0 | D | Major |
| 2 | 36.99s | 27.42s | -9.4 dB | 105.0 | D | Major |
| 3 | 64.41s | 11.42s | -8.4 dB | 105.2 | B | Minor |
| 4 | 75.83s | 21.15s | -6.9 dB | 104.8 | D | Major |
| 5 | 96.98s | 36.00s | -11.4 dB | 105.0 | E | Minor |
| 6 | 132.98s | 17.15s | -13.4 dB | 105.0 | B | Minor |
| 7 | 150.13s | 14.30s | -8.6 dB | 104.9 | B | Minor |
| 8 | 164.43s | 7.41s | -7.0 dB | 105.2 | A | Major |
| 9 | 171.84s | 17.71s | -9.2 dB | 105.0 | B | Minor |
| 10 | 189.55s | 12.57s | -14.9 dB | 104.9 | B | Minor |
| 11 | 202.12s | 8.25s | -13.9 dB | 105.1 | D | Major |

**Example Query - Get sections for a track:**
```sql
SELECT
  section_index,
  ROUND(start, 2) as start_sec,
  ROUND(duration, 2) as duration_sec,
  loudness,
  tempo,
  key,
  CASE mode WHEN 1 THEN 'Major' ELSE 'Minor' END as scale
FROM `nomadkaraoke.karaoke_decide.spotify_audio_analysis_sections`
WHERE spotify_id = '6dOtVTDdiauQNBQEDOtlAB'
ORDER BY section_index
```

---

## Common Join Patterns

### Get full track info with audio features
```sql
SELECT
  t.title,
  t.artist_name,
  t.popularity,
  af.danceability,
  af.energy,
  af.valence,
  af.tempo,
  af.key,
  CASE af.mode WHEN 1 THEN 'Major' ELSE 'Minor' END as scale
FROM `nomadkaraoke.karaoke_decide.spotify_tracks` t
JOIN `nomadkaraoke.karaoke_decide.spotify_audio_features` af
  ON t.spotify_id = af.track_id
WHERE t.popularity > 70
```

### Get tracks with their genres
```sql
SELECT DISTINCT
  t.title,
  t.artist_name,
  g.genre
FROM `nomadkaraoke.karaoke_decide.spotify_tracks` t
JOIN `nomadkaraoke.karaoke_decide.spotify_track_artists` ta
  ON t.spotify_id = ta.track_id
JOIN `nomadkaraoke.karaoke_decide.spotify_artist_genres` g
  ON ta.artist_id = g.artist_id
WHERE t.title = 'Bohemian Rhapsody'
```

### Get audio analysis with track metadata
```sql
SELECT
  t.title,
  t.artist_name,
  aat.tempo,
  aat.key,
  aat.time_signature,
  aat.loudness,
  aat.duration
FROM `nomadkaraoke.karaoke_decide.spotify_audio_analysis_tracks` aat
JOIN `nomadkaraoke.karaoke_decide.spotify_tracks` t
  ON aat.spotify_id = t.spotify_id
WHERE t.popularity > 80
```

### Find tracks in a specific key and tempo range (for karaoke compatibility)
```sql
SELECT
  t.title,
  t.artist_name,
  af.tempo,
  af.key,
  CASE af.mode WHEN 1 THEN 'Major' ELSE 'Minor' END as scale
FROM `nomadkaraoke.karaoke_decide.spotify_tracks` t
JOIN `nomadkaraoke.karaoke_decide.spotify_audio_features` af
  ON t.spotify_id = af.track_id
WHERE af.key = 0  -- C
  AND af.mode = 1  -- Major
  AND af.tempo BETWEEN 100 AND 130
  AND t.popularity > 60
ORDER BY t.popularity DESC
LIMIT 20
```

---

## Feature Ideas

This data enables many karaoke-related features:

### Song Recommendations
- **Similar songs**: Match by tempo, key, energy, danceability
- **Mood-based playlists**: Use valence for happy/sad songs
- **Genre exploration**: Suggest songs from related genres

### Vocal Difficulty
- **Vocal range indicators**: Use key + sections for range estimation
- **Song structure complexity**: Number of sections, key changes
- **Tempo difficulty**: Fast tempo = harder lyrics

### Karaoke Performance
- **Key transposition suggestions**: Based on user's preferred vocal range
- **Section timestamps**: Mark verse/chorus for practice
- **Energy mapping**: Highlight high-energy sections

### Discovery
- **Find songs by audio characteristics**: "Songs like X but in key of Y"
- **Acoustic versions**: High acousticness, low energy
- **Party songs**: High danceability + energy + valence

---

## Data Quality Notes

1. **Popularity scores** are point-in-time snapshots from July 2025
2. **Audio analysis** coverage is partial (~18M of ~256M tracks so far, ETL in progress)
3. **Genre data** is at artist level, not track level
4. **Some tracks** may have missing audio features (null values)
5. **Confidence scores** in audio analysis indicate reliability of detection

## ETL Status

The audio analysis ETL is currently running and will be complete in ~37 hours:
- **Tracks processed**: 262/484 files (54%)
- **Current counts**: 18.4M tracks, 178M sections
- **Expected final**: ~35M tracks, ~340M sections (estimated)
