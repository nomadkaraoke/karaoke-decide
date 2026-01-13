# MLHD+ Data Import for Collaborative Filtering

**Date:** 2026-01-13
**Status:** In Progress (VM Running)
**Branch:** `feat/sess-20260113-mlhd-import`

## Overview

Importing the **MLHD+ (Music Listening Histories Dataset+)** - 27 billion listening events from 583,000 Last.fm users - to power collaborative filtering recommendations ("users who like X also like Y").

## Why MLHD+ Instead of Last.fm API

| Metric | Last.fm API Crawl | MLHD+ Dataset |
|--------|-------------------|---------------|
| Users | 10,000 | **583,000** (58x more) |
| Listening events | ~10M | **27 billion** |
| Unique artists | Unknown | **555,000** |
| Time to acquire | 7-10 hours (rate limited) | Download time only |
| License | ToS limitations | **CC0 (Public Domain)** |

## Infrastructure

### GCS Bucket
```
gs://nomadkaraoke-mlhd-data/
├── raw/                    # Original archives (backup)
│   └── mlhdplus-complete-*.tar
├── processed/              # Per-archive user histories
│   └── mlhdplus-complete-*_histories.json
├── cooccurrence.json       # Final artist similarity matrix
└── artist_stats.json       # Artist listener counts
```

### GCE VM
- **Name:** `mlhd-import`
- **Project:** `nomadkaraoke`
- **Zone:** `us-central1-a`
- **Machine:** `n2-standard-8` (8 vCPU, 32GB RAM)
- **Disk:** 500GB SSD
- **Created:** 2026-01-13 07:29 PST

## Processing Pipeline

### Phase 1: Download & Backup
- Downloads 16 "complete" archives (~15GB each, ~240GB total)
- Immediately backs up each archive to GCS after download
- Resume-capable: checks GCS before downloading

### Phase 2: Extract User Histories
- Extracts each tar archive to temp directory
- Processes ~36k user files per archive (zstd compressed)
- Filters to users with 10+ artists
- Saves per-archive JSON to GCS for checkpointing

### Phase 3: Build Co-occurrence Matrix
- Loads all user histories
- Limits to top 100 artists per user (reduces O(n×m²) explosion)
- Builds artist pair co-occurrence counts
- Filters to pairs with 50+ shared users
- Calculates Jaccard similarity

## Data Format

### Input (MLHD+ user files)
```
<timestamp>\t<artist_mbid>\t<release_mbid>\t<recording_mbid>
```

### Output (cooccurrence.json)
```json
{
  "<mbid_a>|<mbid_b>": {
    "shared": 1234,
    "users_a": 5000,
    "users_b": 3000,
    "jaccard": 0.182
  }
}
```

## Monitoring

### Check VM logs
```bash
gcloud compute ssh mlhd-import --project=nomadkaraoke --zone=us-central1-a \
  --command="tail -50 /var/log/mlhd-import.log"
```

### Check download progress
```bash
gcloud compute ssh mlhd-import --project=nomadkaraoke --zone=us-central1-a \
  --command="ls -lh /data/mlhd/"
```

### Check GCS uploads
```bash
gsutil ls -lh gs://nomadkaraoke-mlhd-data/raw/
gsutil ls -lh gs://nomadkaraoke-mlhd-data/processed/
```

### Check if complete
```bash
gsutil stat gs://nomadkaraoke-mlhd-data/cooccurrence.json
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/vm_startup.sh` | VM startup script (runs full pipeline) |
| `scripts/mlhd_import.py` | Local CLI for download/process/map |
| `scripts/listenbrainz_similar.py` | Query ListenBrainz API for similar artists |

## Next Steps After Import

1. **Build MBID → Spotify ID mapping** - Use MusicBrainz API or ListenBrainz Labs API
2. **Load to BigQuery** - Create `mlhd_artist_similarity` table
3. **Integrate with recommendations** - Update `_get_collaborative_suggestions()` in recommendation service

## Related Documents

- [MLHD-IMPORT-PLAN.md](../MLHD-IMPORT-PLAN.md) - Full technical plan
- [MLHD+ Dataset](https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/) - Source data
- [ListenBrainz Labs API](https://labs.api.listenbrainz.org/) - Pre-computed similar artists

## Cost Estimate

| Resource | Usage | Est. Cost |
|----------|-------|-----------|
| GCS Storage | ~300GB | ~$7/month |
| GCE VM | ~24 hours | ~$3 one-time |
| BigQuery Storage | ~10GB | ~$0.20/month |
| **Total** | | **~$10 one-time + $7/month** |

## Lessons Learned

1. **Always backup raw data first** - Archives are backed up to GCS immediately after download, enabling resume
2. **Checkpoint per-archive** - Processing saves intermediate results to GCS, so we can resume from any point
3. **Limit artists per user** - Top 100 artists per user prevents O(n×m²) explosion in co-occurrence calculation
4. **ListenBrainz API as fallback** - If local processing is too slow, can use pre-computed similar artists API
