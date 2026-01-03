# 2026-01-02: Full Spotify ETL

## Summary

Correcting the previous decision that skipped Audio Analysis ETL. This work:
1. Restores both Spotify torrents from GCS Archive
2. Verifies data integrity via transmission torrent client
3. Downloads any missing pieces from torrent peers
4. Runs incremental ETL extracting track summaries AND sections
5. Seeds both torrents for 1 month to support the community

## Why This Was Needed

The previous PR (#27) stated "The audio analysis ETL was ultimately **not needed**" because `spotify_audio_features` table already existed. However:

1. **Sections data is unique to Audio Analysis** - The sections array provides temporal breakdown of a song (intro, verse, chorus, bridge, outro) with tempo/key/mode changes. This is NOT available in the audio_features table.
2. **Confidence scores** - Audio Analysis includes confidence values for tempo, key, mode, time_signature that aren't in audio_features
3. **Torrent seeding obligation** - We should seed back to the community after downloading 4TB of data
4. **Data integrity verification** - Need to confirm our GCS backup is complete before relying on it

## Current Status (2026-01-03)

| Task | Status | Notes |
|------|--------|-------|
| VM Created | ✅ Complete | `spotify-etl-seed-vm` (e2-standard-8, 5TB SSD) |
| Audio Analysis GCS Restore | ✅ Complete | 3.4TB restored |
| Metadata GCS Restore | ✅ Complete | 187GB restored |
| Folder Reorganization | ✅ Complete | Moved to torrent-expected paths |
| Torrent Verification | ✅ Complete | Data verified against torrent |
| Metadata Torrent | ✅ Seeding | 100% complete, ratio 4.4 |
| Audio Analysis Torrent | 🔄 Downloading | 84.7% (3.29TB), ~600GB missing from GCS |
| Partial ETL | ⏳ Pending | 108 complete files ready (~8M tracks) |
| Full ETL | ⏳ Pending | After download completes |
| GCS Backup (verified) | ⏳ Pending | After 100% download |

## Key Discovery: GCS Backup Was Incomplete

During torrent verification, we discovered the GCS backup was missing ~600GB of data:
- GCS had 3.4TB, torrent expects 3.88TB
- Missing pieces are being downloaded from torrent peers
- ETA: ~16 hours at current speeds (varies with peer availability)
- This validates the importance of torrent verification before relying on GCS backups

## Infrastructure

- **VM:** `spotify-etl-seed-vm` (e2-standard-8, 8 vCPU, 32GB RAM)
- **Disk:** 5TB SSD at `/data` (us-central1-a)
- **GCS Archives:**
  - `gs://nomadkaraoke-raw-archives/spotify-audio-analysis-2025-07/` (3.45 TiB - incomplete)
  - `gs://nomadkaraoke-raw-archives/spotify-metadata-2025-07/` (186 GiB - complete)

## Incremental ETL Plan

### Why Incremental?

- 108 of 484 files (22%) are 100% complete and ready for processing
- Each file contains ~74K tracks with ~10 sections each
- Files are naturally partitioned by spotify_id prefix (no overlap)
- Can process available data now, remainder when download completes

### File Structure

Each `.jsonl.zst` file contains tracks whose spotify_id starts with that 2-character prefix:
- `00.jsonl.zst` → IDs starting with "00" (~74K tracks)
- `0a.jsonl.zst` → IDs starting with "0a" (~74K tracks)
- **Zero overlap between files** - safe for incremental processing

### ETL Output Structure

```
/data/output/
├── processed_files.txt           # Tracking: list of completed files
├── tracks/                       # NDJSON output per source file
│   ├── 00_tracks.ndjson
│   ├── 01_tracks.ndjson
│   └── ...
└── sections/
    ├── 00_sections.ndjson
    ├── 01_sections.ndjson
    └── ...
```

### Process Flow

1. **Check tracking file** - Skip if file already in `processed_files.txt`
2. **Extract to NDJSON** - One output file per input file
3. **Load to BigQuery** - WRITE_APPEND mode
4. **Update tracking** - Record filename in `processed_files.txt`
5. **Repeat** - Process next available file

### Safety Guarantees

- **No data loss** - Each file produces separate output
- **No duplicates** - Files contain non-overlapping spotify_ids
- **Resumable** - Tracking file enables safe restart
- **Verifiable** - Can count processed vs total files
- **Idempotent** - Re-running on same file produces same output

## Scripts Created

| Script | Purpose |
|--------|---------|
| `scripts/spotify_audio_analysis_full_etl.py` | Extract tracks + sections to BigQuery |
| `scripts/setup_seeding.sh` | Configure transmission for seeding |
| `scripts/inspect_metadata_sqlite.py` | Inspect SQLite metadata structure |

## BigQuery Tables

### `spotify_audio_analysis_tracks`
| Field | Type | Description |
|-------|------|-------------|
| spotify_id | STRING | Join key (unique per track) |
| duration | FLOAT | Track duration (seconds) |
| tempo | FLOAT | BPM |
| tempo_confidence | FLOAT | Confidence (0-1) |
| time_signature | INTEGER | Time signature (e.g., 4) |
| time_signature_confidence | FLOAT | Confidence (0-1) |
| key | INTEGER | Musical key (0-11) |
| key_confidence | FLOAT | Confidence (0-1) |
| mode | INTEGER | Major (1) / Minor (0) |
| mode_confidence | FLOAT | Confidence (0-1) |
| loudness | FLOAT | dB level |
| end_of_fade_in | FLOAT | Fade-in end (seconds) |
| start_of_fade_out | FLOAT | Fade-out start (seconds) |
| num_samples | INTEGER | Audio samples |
| analysis_sample_rate | INTEGER | Sample rate |
| analyzer_version | STRING | Spotify analyzer version |
| analysis_time | FLOAT | Analysis processing time |

### `spotify_audio_analysis_sections`
| Field | Type | Description |
|-------|------|-------------|
| spotify_id | STRING | Join key |
| section_index | INTEGER | Section order (0, 1, 2...) |
| start | FLOAT | Start time (seconds) |
| duration | FLOAT | Section duration |
| confidence | FLOAT | Detection confidence |
| loudness | FLOAT | Section loudness |
| tempo | FLOAT | Section BPM |
| tempo_confidence | FLOAT | Confidence |
| key | INTEGER | Section key |
| key_confidence | FLOAT | Confidence |
| mode | INTEGER | Section mode |
| mode_confidence | FLOAT | Confidence |
| time_signature | INTEGER | Section time sig |
| time_signature_confidence | FLOAT | Confidence |

## Commands Reference

```bash
# SSH to VM
gcloud compute ssh spotify-etl-seed-vm --zone=us-central1-a --project=nomadkaraoke

# Check torrent status
transmission-remote -l

# Check individual file completion
transmission-remote -t 1 -f | grep "100%" | wc -l

# Monitor download progress
watch -n 60 'transmission-remote -l'

# Run incremental ETL (after setting up)
source /data/venv/bin/activate
python3 /data/scripts/spotify_audio_analysis_incremental_etl.py
```

## Estimated Costs

- **VM runtime (ongoing):** ~$8/day for e2-standard-8
- **5TB SSD storage:** ~$2/day
- **1 month seeding:** ~$300 total (can downsize VM after ETL)
- **GCS Archive (ongoing):** ~$5/month
- **BigQuery storage:** ~$0.02/GB/month

## Data Verified (Sample from 00.jsonl.zst)

```json
{
  "meta": {"track_id": "00...", "analyzer_version": "4.0.0", ...},
  "track": {"duration": 369.08, "tempo": 116.019, "key": 2, "mode": 1, ...},
  "sections": [{"start": 0, "duration": 10.5, "tempo": 116, "key": 2, ...}, ...]
}
```

- ~74K tracks per file
- ~10 sections per track on average
- Total: ~36M tracks, ~360M sections expected

## Torrent Magnet Links

- **Audio Analysis:** `magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f`
- **Metadata:** `magnet:?xt=urn:btih:4cc9ac59f807dc6bdf95f52ffc86f44272a361a7`

## Next Steps

1. **Run partial ETL** on 108 complete files (~8M tracks)
2. **Continue monitoring** download progress (check every 3 hours)
3. **When 100% complete:** Backup verified data to GCS
4. **Run remaining ETL** on final 376 files (~28M tracks)
5. **Update documentation** with final counts

## Related

- Previous work: [2025-01-01-spotify-audio-analysis-etl-setup.md](2025-01-01-spotify-audio-analysis-etl-setup.md)
- ETL Plan: `docs/plans/2025-01-spotify-audio-analysis-etl.md`
