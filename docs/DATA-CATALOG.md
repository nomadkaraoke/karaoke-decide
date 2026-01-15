# Music Data Catalog

This document describes all music data available in BigQuery for use in features and recommendations.

> **Last Updated:** 2026-01-15 (MusicBrainz recordings + karaoke linking complete)
>
> **Location:** `nomadkaraoke.karaoke_decide.*`

## Data Sources

1. **MusicBrainz Database Dumps** (Primary - refreshable):
   - Full artist catalog from MusicBrainz (~2.78M artists)
   - Recording catalog (~37.5M recordings)
   - Community-curated tags/genres (~693K)
   - MBID↔Spotify ID mappings (~376K)
   - ⚠️ **TODO:** Automate monthly refresh via Cloud Scheduler + ETL pipeline

2. **Spotify July 2025 Dataset** (Enrichment - static snapshot):
   - From [Anna's Archive Spotify dataset](https://annas-archive.org/datasets/spotify_2025_07)
   - **Metadata Torrent** (~200GB): Track, album, artist metadata + audio features
   - **Audio Analysis Torrent** (~3.88TB): Detailed audio analysis with sections, beats, bars, etc.

3. **MLHD+ Artist Similarity** (Recommendations):
   - Derived from Music Listening Histories Dataset (583K Last.fm users)
   - 1.5M artist similarity pairs based on co-occurrence in listening histories
   - Used for "Listeners of X also like Y" recommendations

4. **Karaoke Catalog** (Core feature data):
   - 275K karaoke songs from KaraokeNerds
   - Links to MusicBrainz recordings (58.9% coverage)
   - Source: Daily API export from karaokenerds.com (with permission)
   - ⚠️ **TODO:** Currently a one-off dump - need to automate daily fetch and ETL

## Table Summary

### MusicBrainz Tables (Primary)

| Table | Row Count | Description |
|-------|-----------|-------------|
| `mb_artists` | 2,780,016 | Full MusicBrainz artist catalog |
| `mb_recordings` | 37,530,321 | MusicBrainz recording catalog |
| `mb_artist_tags` | 693,045 | Community-sourced tags/genres |
| `mb_recording_isrc` | 5,480,292 | ISRC codes for recordings |
| `mbid_spotify_mapping` | 376,231 | MBID to Spotify ID mappings |
| `mb_artists_normalized` | 2,780,016 | Pre-joined for fast search |
| `karaoke_recording_links` | 162,314 | Karaoke songs → MB recordings |
| `isrc_spotify_mapping` | 17,012,103 | View: ISRC cross-reference |

### Spotify Tables (Enrichment)

| Table | Row Count | Description |
|-------|-----------|-------------|
| `spotify_track_artists` | 348M | Track-to-artist mapping (many-to-many) |
| `spotify_audio_analysis_sections` | **325M** | Song sections with timing and musical properties |
| `spotify_tracks` | 256M | Track metadata (title, artist, popularity) |
| `spotify_audio_features` | 230M | Audio features (danceability, energy, etc.) |
| `spotify_albums` | 59M | Album metadata |
| `spotify_audio_analysis_tracks` | **33.5M** | Track-level audio analysis summary |
| `spotify_artists` | 15M | Artist metadata |
| `spotify_artist_genres` | 2.2M | Artist-to-genre mapping (768 unique genres) |

### MLHD+ Tables (Recommendations)

| Table | Row Count | Description |
|-------|-----------|-------------|
| `mlhd_artist_similarity` | 1,505,697 | Artist pairs with similarity scores |

### Karaoke Catalog

| Table | Row Count | Description |
|-------|-----------|-------------|
| `karaokenerds_raw` | 275,809 | Full karaoke song catalog |

---

## MusicBrainz Tables

### mb_artists

Primary artist catalog from MusicBrainz database dumps.

| Column | Type | Description |
|--------|------|-------------|
| `artist_mbid` | STRING | MusicBrainz UUID (primary key) |
| `name` | STRING | Artist name |
| `sort_name` | STRING | Name for alphabetical sorting |
| `disambiguation` | STRING | Disambiguator (e.g., "UK rock band") |
| `artist_type` | STRING | "Person", "Group", "Orchestra", etc. |
| `begin_year` | INT64 | Year artist/band started |
| `end_year` | INT64 | Year artist/band ended (if applicable) |
| `area_name` | STRING | Country or region |
| `gender` | STRING | For solo artists |
| `name_normalized` | STRING | Lowercase normalized for search |

**Example Query - Search artists:**
```sql
SELECT artist_mbid, name, disambiguation, artist_type
FROM `nomadkaraoke.karaoke_decide.mb_artists`
WHERE name_normalized LIKE 'radiohead%'
LIMIT 10
```

### mb_artist_tags

Community-sourced tags (genres, styles, descriptors).

| Column | Type | Description |
|--------|------|-------------|
| `artist_mbid` | STRING | MusicBrainz UUID |
| `tag_name` | STRING | Tag name (e.g., "alternative rock") |
| `vote_count` | INT64 | Community vote count |

**Example Query - Get top tags for an artist:**
```sql
SELECT tag_name, vote_count
FROM `nomadkaraoke.karaoke_decide.mb_artist_tags`
WHERE artist_mbid = 'a74b1b7f-71a5-4011-9441-d0b5e4122711'  -- Radiohead
ORDER BY vote_count DESC
LIMIT 5
```

### mbid_spotify_mapping

Maps MusicBrainz IDs to Spotify artist IDs.

| Column | Type | Description |
|--------|------|-------------|
| `artist_mbid` | STRING | MusicBrainz UUID |
| `spotify_artist_id` | STRING | Spotify artist ID |
| `artist_name` | STRING | Artist name (for reference) |

### mb_artists_normalized

Pre-joined view for efficient artist search with Spotify enrichment.

| Column | Type | Description |
|--------|------|-------------|
| `artist_mbid` | STRING | MusicBrainz UUID |
| `artist_name` | STRING | Artist name |
| `name_normalized` | STRING | Lowercase normalized |
| `disambiguation` | STRING | Disambiguator |
| `artist_type` | STRING | Type of artist |
| `begin_year` | INT64 | Start year |
| `area_name` | STRING | Country/region |
| `spotify_artist_id` | STRING | Spotify ID (nullable) |
| `popularity` | INT64 | Spotify popularity (default 50) |
| `spotify_genres` | ARRAY<STRING> | Spotify genres |
| `mb_tags` | ARRAY<STRING> | Top 5 MusicBrainz tags |

**Example Query - Search with enrichment:**
```sql
SELECT artist_mbid, artist_name, popularity, mb_tags, spotify_genres
FROM `nomadkaraoke.karaoke_decide.mb_artists_normalized`
WHERE name_normalized LIKE 'green%'
  AND popularity >= 60
ORDER BY popularity DESC
LIMIT 10
```

### mb_recordings

MusicBrainz recording catalog (songs/tracks).

| Column | Type | Description |
|--------|------|-------------|
| `recording_mbid` | STRING | MusicBrainz UUID (primary key) |
| `title` | STRING | Recording title |
| `length_ms` | INT64 | Duration in milliseconds |
| `artist_credit` | STRING | Display string (e.g., "Artist feat. Other") |
| `artist_credit_id` | INT64 | FK for future use |
| `disambiguation` | STRING | Disambiguator (e.g., "live version") |
| `video` | BOOLEAN | Whether this is a video recording |
| `name_normalized` | STRING | Lowercase normalized for search |

**Example Query - Search recordings:**
```sql
SELECT recording_mbid, title, artist_credit
FROM `nomadkaraoke.karaoke_decide.mb_recordings`
WHERE name_normalized LIKE 'bohemian%'
LIMIT 10
```

### mb_recording_isrc

ISRC codes for MusicBrainz recordings (for cross-referencing with Spotify).

| Column | Type | Description |
|--------|------|-------------|
| `recording_mbid` | STRING | MusicBrainz recording UUID |
| `isrc` | STRING | ISRC code (12 characters) |

**Example Query - Find recording by ISRC:**
```sql
SELECT ri.recording_mbid, r.title, r.artist_credit
FROM `nomadkaraoke.karaoke_decide.mb_recording_isrc` ri
JOIN `nomadkaraoke.karaoke_decide.mb_recordings` r
  ON ri.recording_mbid = r.recording_mbid
WHERE ri.isrc = 'GBAYE0601498'  -- Bohemian Rhapsody
```

### karaoke_recording_links

Links karaoke catalog songs to MusicBrainz recordings.

| Column | Type | Description |
|--------|------|-------------|
| `karaoke_id` | INT64 | FK to karaokenerds_raw.Id |
| `recording_mbid` | STRING | MusicBrainz recording UUID |
| `spotify_track_id` | STRING | Spotify track ID (if matched via ISRC) |
| `match_method` | STRING | "isrc" or "exact_name" |
| `match_confidence` | FLOAT64 | 0.95 for ISRC, 0.80 for name match |

**Coverage:** 162,314 / 275,809 karaoke songs (58.9%)

**Example Query - Get linked recordings for karaoke songs:**
```sql
SELECT k.Artist, k.Title, krl.recording_mbid, krl.match_method
FROM `nomadkaraoke.karaoke_decide.karaokenerds_raw` k
JOIN `nomadkaraoke.karaoke_decide.karaoke_recording_links` krl
  ON k.Id = krl.karaoke_id
WHERE k.Artist = 'Queen'
```

### isrc_spotify_mapping (View)

Cross-reference view joining MB recordings to Spotify tracks via ISRC.

| Column | Type | Description |
|--------|------|-------------|
| `isrc` | STRING | Shared ISRC code |
| `recording_mbid` | STRING | MusicBrainz recording UUID |
| `spotify_track_id` | STRING | Spotify track ID |
| `spotify_title` | STRING | Spotify track title |
| `spotify_artist` | STRING | Spotify artist name |
| `mb_title` | STRING | MusicBrainz recording title |
| `mb_artist` | STRING | MusicBrainz artist credit |

**Example Query - Find Spotify track for MB recording:**
```sql
SELECT *
FROM `nomadkaraoke.karaoke_decide.isrc_spotify_mapping`
WHERE recording_mbid = 'some-uuid'
```

---

## Spotify Metadata Tables

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

## MLHD+ Similarity Data

### mlhd_artist_similarity

Artist similarity pairs derived from Music Listening Histories Dataset (583K Last.fm users). Based on co-occurrence: artists that appear together in listening histories are considered similar.

| Column | Type | Description |
|--------|------|-------------|
| `artist_mbid_0` | STRING | First artist MusicBrainz UUID |
| `artist_mbid_1` | STRING | Second artist MusicBrainz UUID |
| `similarity_score` | FLOAT64 | Similarity score (higher = more similar) |

**Stats:** 1,505,697 artist pairs

**Example Query - Find similar artists:**
```sql
SELECT
    s.artist_mbid_1 as similar_artist_mbid,
    a.name as similar_artist_name,
    s.similarity_score
FROM `nomadkaraoke.karaoke_decide.mlhd_artist_similarity` s
JOIN `nomadkaraoke.karaoke_decide.mb_artists` a
    ON s.artist_mbid_1 = a.artist_mbid
WHERE s.artist_mbid_0 = '084308bd-1654-436f-ba03-df6697104e19'  -- Green Day
ORDER BY s.similarity_score DESC
LIMIT 10
```

---

## Karaoke Catalog

### karaokenerds_raw

Full karaoke song catalog from KaraokeNerds.com (fetched via API with permission).

| Column | Type | Description |
|--------|------|-------------|
| `Id` | INT64 | Unique song ID |
| `Artist` | STRING | Artist name |
| `Title` | STRING | Song title |
| `Brand` | STRING | Karaoke brand (Sunfly, Zoom, etc.) |
| `DiscId` | STRING | Disc identifier |
| `TrackNo` | INT64 | Track number on disc |

**Stats:** 275,809 songs

**Example Query - Search karaoke songs:**
```sql
SELECT Id, Artist, Title, Brand
FROM `nomadkaraoke.karaoke_decide.karaokenerds_raw`
WHERE LOWER(Artist) LIKE '%queen%'
ORDER BY Title
LIMIT 20
```

**Example Query - Get karaoke songs with MB recording links:**
```sql
SELECT
    k.Artist,
    k.Title,
    k.Brand,
    krl.recording_mbid,
    krl.match_confidence
FROM `nomadkaraoke.karaoke_decide.karaokenerds_raw` k
LEFT JOIN `nomadkaraoke.karaoke_decide.karaoke_recording_links` krl
    ON k.Id = krl.karaoke_id
WHERE LOWER(k.Artist) = 'queen'
ORDER BY k.Title
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
2. **Audio analysis** covers 33.5M tracks (~13% of all Spotify tracks) - these are tracks that had audio analysis data in the source dataset
3. **Genre data** is at artist level, not track level
4. **Some tracks** may have missing audio features (null values)
5. **Confidence scores** in audio analysis indicate reliability of detection

## Data Backup

Raw torrent data is archived in GCS for future re-processing if needed:
- **Location:** `gs://nomadkaraoke-raw-archives/spotify-audio-analysis-2025-07/`
- **Size:** 6.97 TiB (484 .zst files)
- **Storage class:** Archive (~$7/month)
