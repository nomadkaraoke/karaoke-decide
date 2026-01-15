# MLHD+ and MusicBrainz Data Import

**Date:** 2026-01-13
**Last Updated:** 2026-01-14 23:30 UTC
**Status:** ✅ COMPLETE - Data loaded to BigQuery and integrated into quiz service

## Summary

Successfully imported listening history data from 583,000 Last.fm users to enhance quiz recommendations with collaborative filtering.

### Final Results

| Metric | Value |
|--------|-------|
| Total users processed | 594,370 |
| Unique artists | 489,578 |
| Artist pairs (50+ shared users) | 1,805,782 |
| MBID → Spotify mappings | 376,231 |
| **Final similarity pairs (with Spotify IDs)** | **1,542,594** |

### BigQuery Tables Created

| Table | Rows | Description |
|-------|------|-------------|
| `karaoke_decide.mlhd_artist_similarity` | 1,542,594 | Artist pairs with Spotify IDs and similarity scores |
| `karaoke_decide.mbid_spotify_mapping` | 376,231 | MusicBrainz ID to Spotify ID mapping |
| `karaoke_decide.mlhd_cooccurrence_staging` | 1,805,782 | Raw co-occurrence data (MBIDs) |

### Integration

The MLHD data is now integrated into the quiz recommendation service:
- Added `_get_mlhd_similar_artists()` method in `backend/services/quiz_service.py`
- Queries BigQuery for artists similar to user's selections
- Shows "Listeners of X also like" suggestions in the quiz
- Priority: after karaoke singers and ListenBrainz, before genre matching

## Quick Reference

```bash
# Query similar artists in BigQuery
SELECT artist_a_name, artist_b_name, shared_users, jaccard_similarity
FROM karaoke_decide.mlhd_artist_similarity
WHERE artist_a_name = 'Green Day' OR artist_b_name = 'Green Day'
ORDER BY shared_users DESC
LIMIT 10;

# Check table sizes
SELECT table_id, row_count FROM karaoke_decide.__TABLES__
WHERE table_id LIKE 'mlhd%' OR table_id = 'mbid_spotify_mapping';
```

## Completed Status

### ✅ MusicBrainz Import - COMPLETE
- 376,231 MBID → Spotify ID mappings
- Files in `gs://nomadkaraoke-musicbrainz-data/processed/`

### ✅ MLHD+ Processing - COMPLETE
- All 16 archives processed (594,370 users)
- Co-occurrence matrix built (1.8M pairs)
- Loaded to BigQuery with Spotify ID mapping
- Integrated into quiz service
- **VM deleted** (2026-01-14)

## Technical Notes

### Streaming Zstd Fix (2026-01-14)
The MLHD+ user files use streaming zstd compression (no content size header).
Must use `dctx.stream_reader(f).read()` instead of `dctx.decompress()`.

### MusicBrainz Import

| Property | Value |
|----------|-------|
| VM Name | `musicbrainz-import` |
| Machine | e2-standard-4 (4 vCPU, 16GB RAM) |
| Log File | `/var/log/musicbrainz-import.log` |
| Script | `/tmp/musicbrainz_import.sh` |
| SSH Note | Standard SSH works |

**What it does:**
1. Downloads MusicBrainz PostgreSQL dump (mbdump.tar.bz2, mbdump-derived.tar.bz2)
2. Extracts relevant tables (artist, artist_tag, l_artist_artist, l_artist_url, url)
3. Processes artist tags (community-sourced genres)
4. Processes artist relationships (member_of, collaboration, tribute, etc.)
5. Extracts MBID → Spotify ID mappings from URL relations
6. Uploads JSON files to GCS

**Completion indicator:**
```bash
gsutil stat gs://nomadkaraoke-musicbrainz-data/processed/mbid_spotify_mapping.json
```

**Output files:**
- `gs://nomadkaraoke-musicbrainz-data/processed/artist_tags.json` - Artist genres
- `gs://nomadkaraoke-musicbrainz-data/processed/artist_relationships.json` - Band members, collaborations
- `gs://nomadkaraoke-musicbrainz-data/processed/mbid_spotify_mapping.json` - MBID to Spotify ID

---

## Overview

This session imports two critical datasets for collaborative filtering:

1. **MLHD+ (Music Listening Histories Dataset+)** - 27 billion listening events from 583,000 Last.fm users
2. **MusicBrainz Database** - Artist metadata, relationships, tags, and Spotify ID mappings

## Why MLHD+ Instead of Last.fm API

| Metric | Last.fm API Crawl | MLHD+ Dataset |
|--------|-------------------|---------------|
| Users | 10,000 | **583,000** (58x more) |
| Listening events | ~10M | **27 billion** |
| Unique artists | Unknown | **555,000** |
| Time to acquire | 7-10 hours (rate limited) | Download time only |
| License | ToS limitations | **CC0 (Public Domain)** |

## Infrastructure

### GCS Buckets

**MLHD+ Data:**
```
gs://nomadkaraoke-mlhd-data/
├── raw/                    # Original archives (240GB total) ✓ COMPLETE
│   └── mlhdplus-complete-*.tar
├── processed/              # Per-archive user histories
│   └── mlhdplus-complete-*_histories.json
├── cooccurrence.json       # Final artist similarity matrix
└── artist_stats.json       # Artist listener counts
```

**MusicBrainz Data:**
```
gs://nomadkaraoke-musicbrainz-data/
├── raw/                    # Original dump files
│   └── <DUMP_DATE>/
│       ├── mbdump.tar.bz2
│       └── mbdump-derived.tar.bz2
└── processed/
    ├── artist_tags.json           # Artist -> genres
    ├── artist_relationships.json  # Artist -> artist relations
    └── mbid_spotify_mapping.json  # MBID -> Spotify ID
```

### GCE VMs

| VM | Machine | Disk | Purpose |
|----|---------|------|---------|
| `mlhd-import` | n2-standard-8 (8 vCPU, 32GB) | 500GB SSD | MLHD+ processing |
| `musicbrainz-import` | e2-standard-4 (4 vCPU, 16GB) | 200GB SSD | MusicBrainz import |

Both VMs in **Project:** `nomadkaraoke`, **Zone:** `us-central1-a`

## Processing Pipeline

### MLHD+ Pipeline

**Phase 1: Download & Backup** ✓ COMPLETE
- Downloaded 16 "complete" archives (~15GB each, ~240GB total)
- Backed up each archive to GCS immediately after download
- All files in `gs://nomadkaraoke-mlhd-data/raw/`

**Phase 2: Extract User Histories** (IN PROGRESS)
- Extracts each tar archive to temp directory
- Processes ~36k user files per archive (zstd compressed)
- Filters to users with 10+ artists
- Saves per-archive JSON to GCS for checkpointing

**Phase 3: Build Co-occurrence Matrix** (PENDING)
- Loads all user histories
- Limits to top 100 artists per user (reduces O(n×m²) explosion)
- Builds artist pair co-occurrence counts
- Filters to pairs with 50+ shared users
- Calculates Jaccard similarity

### MusicBrainz Pipeline (IN PROGRESS)

1. Download latest dump from data.metabrainz.org
2. Extract and process tables
3. Build JSON outputs
4. Upload to GCS

## Data Formats

### MLHD+ Input (user files)
```
<timestamp>\t<artist_mbid>\t<release_mbid>\t<recording_mbid>
```

### MLHD+ Output (cooccurrence.json)
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

### MusicBrainz Output (mbid_spotify_mapping.json)
```json
{
  "<mbid>": {
    "spotify_id": "7oPftvlwr6VrsViSDV7fJY",
    "name": "Green Day"
  }
}
```

## Troubleshooting

### If MLHD+ process stopped

```bash
# SSH into VM (with IAP tunnel)
gcloud compute ssh mlhd-import --project=nomadkaraoke --zone=us-central1-a \
  --tunnel-through-iap

# Check if process is running
ps aux | grep python

# If not running, restart:
sudo nohup bash /tmp/mlhd_process.sh > /var/log/mlhd-process.log 2>&1 &

# Monitor
tail -f /var/log/mlhd-process.log
```

### If MusicBrainz process stopped

```bash
# SSH into VM
gcloud compute ssh musicbrainz-import --project=nomadkaraoke --zone=us-central1-a

# Check if process is running
ps aux | grep -E 'python|wget|pbzip2'

# If not running, restart:
sudo nohup bash /tmp/musicbrainz_import.sh > /var/log/musicbrainz-import.log 2>&1 &

# Monitor
tail -f /var/log/musicbrainz-import.log
```

### Check GCS Progress

```bash
# MLHD+ processed archives (should be 16 when complete)
gsutil ls gs://nomadkaraoke-mlhd-data/processed/ | wc -l

# MusicBrainz files
gsutil ls -lh gs://nomadkaraoke-musicbrainz-data/processed/
```

## After Jobs Complete

1. **Delete VMs** to stop billing:
   ```bash
   gcloud compute instances delete mlhd-import --project=nomadkaraoke --zone=us-central1-a
   gcloud compute instances delete musicbrainz-import --project=nomadkaraoke --zone=us-central1-a
   ```

2. **Load to BigQuery** - Create `mlhd_artist_similarity` table

3. **Integrate with recommendations** - Update `_get_collaborative_suggestions()` in recommendation service

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/vm_startup.sh` | Original VM startup script |
| `scripts/mlhd_import.py` | Local CLI for download/process/map |
| `scripts/listenbrainz_similar.py` | Query ListenBrainz API for similar artists |
| `/tmp/mlhd_process.sh` (on VM) | Phase 2 processing script |
| `/tmp/musicbrainz_import.sh` (on VM) | MusicBrainz import script |

## Related Documents

- [MLHD-IMPORT-PLAN.md](../MLHD-IMPORT-PLAN.md) - Full technical plan
- [MLHD+ Dataset](https://data.musicbrainz.org/pub/musicbrainz/listenbrainz/mlhd/) - Source data
- [ListenBrainz Labs API](https://labs.api.listenbrainz.org/) - Pre-computed similar artists
- [MusicBrainz Data Dumps](https://data.metabrainz.org/pub/musicbrainz/data/fullexport/) - Database dumps

## Cost Estimate

| Resource | Usage | Est. Cost |
|----------|-------|-----------|
| GCS Storage (MLHD+) | ~300GB | ~$7/month |
| GCS Storage (MusicBrainz) | ~50GB | ~$1/month |
| GCE VM (mlhd-import) | ~24 hours | ~$3 one-time |
| GCE VM (musicbrainz-import) | ~6 hours | ~$0.50 one-time |
| BigQuery Storage | ~10GB | ~$0.20/month |
| **Total** | | **~$4 one-time + $8/month** |

## Lessons Learned

1. **Use wget for large downloads** - httpx can stall; wget with `--continue` is more reliable
2. **IAP tunnel for some VMs** - The `mlhd-import` VM requires `--tunnel-through-iap` flag
3. **Always backup raw data first** - Archives are backed up to GCS immediately after download
4. **Checkpoint per-archive** - Processing saves intermediate results, enabling resume
5. **Limit artists per user** - Top 100 artists prevents O(n×m²) explosion in co-occurrence
6. **Run parallel imports** - MusicBrainz and MLHD+ on separate VMs saves time
