# 2026-01-02: Full Spotify ETL (In Progress)

## Summary

Correcting the previous decision that skipped Audio Analysis ETL. This work:
1. Restores both Spotify torrents from GCS Archive
2. Verifies data integrity via transmission torrent client
3. Downloads any missing pieces (including `spotify_artist_redirects.json`)
4. Runs full ETL extracting track summaries AND sections
5. Seeds both torrents for 1 month to support the community

## Why This Was Needed

The previous PR (#27) stated "The audio analysis ETL was ultimately **not needed**" because `spotify_audio_features` table already existed. However:

1. **Sections data is unique to Audio Analysis** - The sections array provides temporal breakdown of a song (intro, verse, chorus, bridge, outro) with tempo/key/mode changes. This is NOT available in the audio_features table.
2. **Confidence scores** - Audio Analysis includes confidence values for tempo, key, mode, time_signature that aren't in audio_features
3. **Torrent seeding obligation** - We should seed back to the community after downloading 4TB of data
4. **Data integrity verification** - Need to confirm our GCS backup is complete before relying on it

## Current Status

| Task | Status | Notes |
|------|--------|-------|
| VM Created | ✅ Complete | `spotify-etl-seed-vm` (e2-standard-8, 5TB SSD) |
| Audio Analysis GCS Restore | 🔄 In Progress | 3.4TB total, ~300 MB/s |
| Metadata GCS Restore | ⏳ Pending | 186GB, starts after audio |
| Folder Reorganization | ⏳ Pending | Move to torrent-expected paths |
| Torrent Verification | ⏳ Pending | Transmission will verify + download missing |
| Seeding | ⏳ Pending | 1 month planned |
| ETL to BigQuery | ⏳ Pending | Tracks + Sections tables |

## Infrastructure

- **VM:** `spotify-etl-seed-vm` (e2-standard-8, 8 vCPU, 32GB RAM)
- **Disk:** 5TB SSD at `/data` (us-central1-a)
- **GCS Archives:**
  - `gs://nomadkaraoke-raw-archives/spotify-audio-analysis-2025-07/` (3.45 TiB)
  - `gs://nomadkaraoke-raw-archives/spotify-metadata-2025-07/` (186 GiB)

## Data Issue Discovered

GCS metadata backup is **missing one file**:
- `spotify_artist_redirects.json` (282 KB) not uploaded to GCS
- Transmission will download this during verification

## Scripts Created

| Script | Purpose |
|--------|---------|
| `scripts/spotify_audio_analysis_full_etl.py` | Extract tracks + sections to BigQuery |
| `scripts/setup_seeding.sh` | Configure transmission for seeding |
| `scripts/inspect_metadata_sqlite.py` | Inspect SQLite metadata structure |

## BigQuery Tables (To Be Created)

### `spotify_audio_analysis_tracks`
| Field | Type | Description |
|-------|------|-------------|
| spotify_id | STRING | Join key |
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

## Commands to Continue

```bash
# SSH to VM
gcloud compute ssh spotify-etl-seed-vm --zone=us-central1-a --project=nomadkaraoke

# Check GCS restore progress
tail -f /data/restore.log

# After GCS restore completes:
/data/scripts/prepare_torrents.sh
/data/scripts/setup_transmission_verify.sh

# Monitor torrent verification
watch -n 10 'transmission-remote -l'

# After verification (100% Seeding):
source /data/venv/bin/activate
python3 /data/scripts/spotify_audio_analysis_full_etl.py
```

## Estimated Costs

- **VM runtime (24-48 hrs ETL + verification):** ~$15
- **5TB SSD storage (48 hrs):** ~$3
- **1 month seeding:** ~$200 (can be reduced by downsizing VM after ETL)
- **GCS Archive (ongoing):** ~$5/month

## Torrent Magnet Links

- **Audio Analysis:** `magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f`
- **Metadata:** `magnet:?xt=urn:btih:4cc9ac59f807dc6bdf95f52ffc86f44272a361a7`

## Related

- Previous work: [2025-01-01-spotify-audio-analysis-etl-setup.md](2025-01-01-spotify-audio-analysis-etl-setup.md)
- ETL Plan: `docs/plans/2025-01-spotify-audio-analysis-etl.md`
