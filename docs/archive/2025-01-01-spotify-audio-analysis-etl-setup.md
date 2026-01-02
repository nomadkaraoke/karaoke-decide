# 2025-01-01: Spotify Audio Analysis ETL Setup

## Summary

Downloaded 4TB Spotify Audio Analysis torrent and preserved raw data in GCS Archive. The audio analysis ETL was ultimately **not needed** because the simpler `spotify_audio_features` table (from metadata dump) already contains all the fields needed for karaoke filtering (tempo, key, mode, energy, etc.).

## Final Outcome

| Item | Status |
|------|--------|
| Torrent download | ✅ Complete (3.6 TB) |
| Raw data preservation | ✅ 3.45 TiB in GCS Archive |
| BigQuery ETL | ⏭️ Not needed - audio features table exists |
| VM cleanup | ✅ Deleted |

## What Was Done

### Planning
- Created comprehensive ETL plan: `docs/plans/2025-01-spotify-audio-analysis-etl.md`
- Identified key insight: only need track-level summaries (~99% data reduction)
- Estimated ~40M tracks with audio features

### Scripts Created
- `scripts/spotify_audio_analysis_etl.py` - Python ETL script that:
  - Extracts only track-level fields (tempo, key, mode, loudness, etc.)
  - Skips massive bars/beats/segments/tatums arrays
  - Processes files in parallel
  - Uploads to GCS and loads to BigQuery
- `scripts/setup_audio_analysis_vm.sh` - One-command VM setup

### Infrastructure Launched
- **VM**: `spotify-etl-vm` (e2-standard-8, 8 vCPU, 32GB RAM)
- **Storage**: 5TB SSD at `/data`
- **Region**: us-central1-a (same as BigQuery)
- **Python env**: Virtual environment at `/data/venv`

### Torrent Download Completed
- Download speed: ~28-107 MB/s
- Total size: 3.6 TB
- Completed: 2026-01-02 05:13 EST

## Data Schema

The ETL extracts these fields per track:

| Field | Type | Use Case |
|-------|------|----------|
| spotify_id | STRING | Join key |
| tempo | FLOAT | BPM filtering |
| key | INTEGER | Musical key (0-11) |
| mode | INTEGER | Major (1) / Minor (0) |
| loudness | FLOAT | Derive energy |
| time_signature | INTEGER | Display |
| duration | FLOAT | Validation |

## Files Changed

| File | Change |
|------|--------|
| `docs/plans/2025-01-spotify-audio-analysis-etl.md` | New - full plan |
| `scripts/spotify_audio_analysis_etl.py` | New - ETL script |
| `scripts/setup_audio_analysis_vm.sh` | New - VM setup |
| `docs/README.md` | Updated next steps |

## Commands to Continue

### Monitor Download
```bash
gcloud compute ssh spotify-etl-vm --zone=us-central1-a --project=nomadkaraoke \
  --command='tail -5 /data/torrent.log | grep -E "^\[#"; du -sh /data/annas_archive*'
```

### Run ETL After Download
```bash
gcloud compute ssh spotify-etl-vm --zone=us-central1-a --project=nomadkaraoke
source /data/venv/bin/activate
python3 /data/scripts/spotify_audio_analysis_etl.py
```

### Cleanup After ETL
```bash
gcloud compute instances delete spotify-etl-vm --zone=us-central1-a --project=nomadkaraoke
```

## Why Audio Analysis ETL Was Not Needed

The `spotify_audio_features` table (229.5M rows) was loaded from the Spotify metadata dump and contains all the fields we need:

| Field | Type | Description |
|-------|------|-------------|
| track_id | STRING | Join key |
| tempo | FLOAT64 | BPM |
| key | INT64 | Musical key (0-11) |
| mode | INT64 | Major (1) / Minor (0) |
| energy | FLOAT64 | Energy level (0-1) |
| danceability | FLOAT64 | Danceability (0-1) |
| loudness | FLOAT64 | dB level |
| valence | FLOAT64 | Positivity (0-1) |
| acousticness | FLOAT64 | Acoustic probability |
| instrumentalness | FLOAT64 | Instrumental probability |
| liveness | FLOAT64 | Live recording probability |
| speechiness | FLOAT64 | Spoken word probability |
| duration_ms | INT64 | Track duration |
| time_signature | INT64 | Time signature |

The Audio Analysis API provides more granular data (bars, beats, segments, sections) but the track-level features are already available in the simpler Audio Features API data.

## Remaining Work

1. ~~Wait for torrent download to complete~~ ✅
2. ~~Wait for GCS Archive upload to complete~~ ✅
3. ~~Run ETL script~~ ⏭️ Not needed
4. Create BigQuery view joining tracks with audio features
5. Update API to expose audio features in search/filters

## Raw Data Preservation

**Lesson learned:** Previous metadata ETL only extracted certain fields, requiring re-download later. To avoid this, raw torrent data is being preserved in GCS Archive storage.

- **Bucket:** `gs://nomadkaraoke-raw-archives/spotify-audio-analysis-2025-07/`
- **Storage class:** Archive ($0.0012/GB/month)
- **Monthly cost:** ~$5/month for 4TB
- **Purpose:** Future feature development may need per-segment data (beats, bars, sections)

## Actual Costs

- VM runtime (~10 hrs): ~$3
- SSD storage (5TB, ~10 hrs): ~$0.50
- GCS Archive storage (3.45 TiB, ongoing): ~$4/month
- **Total one-time**: ~$4
- **Total ongoing**: ~$4/month

## PR

https://github.com/nomadkaraoke/karaoke-decide/pull/27
