# 2025-01-01: Spotify Audio Analysis ETL Setup

## Summary

Set up infrastructure and started ETL process to load 4TB Spotify Audio Analysis data into BigQuery. This data will enable filtering karaoke songs by tempo, key, mode, and energy.

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

### Torrent Download Started
- Download speed: ~28 MB/s
- Total size: 3.6 TB
- ETA: 36-48 hours from start time

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

## Pending Work

1. Wait for torrent download to complete (~5-6 hrs remaining)
2. Wait for GCS Archive upload to complete
3. Run ETL script to extract track-level summaries to BigQuery
4. Verify BigQuery table created
5. Add table to Pulumi infrastructure
6. Create BigQuery view joining tracks with audio analysis
7. Update API to expose audio features in search/filters

## Raw Data Preservation

**Lesson learned:** Previous metadata ETL only extracted certain fields, requiring re-download later. To avoid this, raw torrent data is being preserved in GCS Archive storage.

- **Bucket:** `gs://nomadkaraoke-raw-archives/spotify-audio-analysis-2025-07/`
- **Storage class:** Archive ($0.0012/GB/month)
- **Monthly cost:** ~$5/month for 4TB
- **Purpose:** Future feature development may need per-segment data (beats, bars, sections)

## Cost Estimate

- VM runtime (72-96 hrs): ~$20-30
- SSD storage (5TB, 4 days): ~$3-4
- GCS Archive storage (4TB, ongoing): ~$5/month
- **Total one-time**: ~$25-35
- **Total ongoing**: ~$5/month

## PR

https://github.com/nomadkaraoke/karaoke-decide/pull/27
