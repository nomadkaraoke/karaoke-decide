# Spotify Audio Analysis ETL Plan

> **Purpose**: Load Spotify audio analysis data (4TB torrent) into BigQuery to enable filtering karaoke songs by tempo, key, energy, and other musical attributes.

## Background

### Current State
- **275K karaoke songs** in `karaoke_decide.karaokenerds_raw`
- **256M Spotify tracks** in `karaoke_decide.spotify_tracks` (basic metadata only)
- No audio features data currently loaded

### Previous ETL (Dec 2024)
Successfully loaded the Spotify metadata dump (186GB compressed → 256M tracks) using:
- GCE VM (e2-standard-4, 200GB SSD) in us-central1
- Downloaded torrent, decompressed zstd, extracted to NDJSON, uploaded to GCS, loaded to BigQuery
- Cost: ~$20-30 for compute + storage

### New Data Source
- **Torrent**: `magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f&dn=annas_archive_spotify_2025_07_audio_analysis.torrent`
- **Size**: ~4TB compressed (JSON lines with zstd compression)
- **Content**: Raw Spotify Audio Analysis API responses (~40M tracks)
- **Format**: `##.json.zst` files containing JSON lines

### Audio Analysis Data Structure

From Spotify's [Audio Analysis API](https://developer.spotify.com/documentation/web-api/reference/get-audio-analysis):

```json
{
  "meta": { "analyzer_version": "...", "platform": "...", "analysis_time": ... },
  "track": {
    "duration": 255.34898,
    "tempo": 98.002,
    "tempo_confidence": 0.423,
    "time_signature": 4,
    "time_signature_confidence": 1.0,
    "key": 5,
    "key_confidence": 0.36,
    "mode": 0,
    "mode_confidence": 0.414,
    "loudness": -5.883,
    "end_of_fade_in": 0.0,
    "start_of_fade_out": 251.73
  },
  "bars": [...],      // Hundreds of bar markers
  "beats": [...],     // Thousands of beat markers
  "sections": [...],  // 10-20 sections (chorus, verse, etc.)
  "segments": [...],  // Thousands of segments with pitch/timbre vectors
  "tatums": [...]     // Thousands of tatum markers
}
```

## Goals

### Primary Goal
Enable users to filter karaoke recommendations by:
- **Tempo/BPM** (slow ballad vs. high-energy)
- **Key** (C, C#, D, etc.)
- **Mode** (major vs. minor)
- **Energy** (derived from loudness)

### Secondary Goals
- Match audio analysis to existing karaoke catalog
- Prepare for future vocal range detection feature (sections data may help)

## Approach

### Data Extraction Strategy

**Critical insight**: We don't need the full audio analysis data. The `bars`, `beats`, `segments`, and `tatums` arrays contain thousands of entries per track and account for ~99% of the data size. For karaoke filtering, we only need the **track-level summary**.

#### What We Need (Per Track)
| Field | Source | Use Case |
|-------|--------|----------|
| `spotify_id` | filename or meta | Join key |
| `tempo` | track.tempo | Filter by BPM |
| `time_signature` | track.time_signature | Display info |
| `key` | track.key | Filter by key |
| `mode` | track.mode | Major/minor filter |
| `loudness` | track.loudness | Derive energy |
| `duration` | track.duration | Validation |

#### What We Skip
- `bars[]` - Time markers (not needed)
- `beats[]` - Time markers (not needed)
- `segments[]` - Per-segment pitch/timbre (not needed for MVP)
- `tatums[]` - Time markers (not needed)
- `sections[]` - Could be useful later, but skip for now

### Size Reduction Estimate
- Full audio analysis per track: ~50-200KB
- Track summary only: ~200 bytes
- **Expected reduction: 99%+**
- 40M tracks × 200 bytes = ~8GB final data (vs. 4TB raw)

## Infrastructure Plan

### VM Specification

| Resource | Specification | Rationale |
|----------|---------------|-----------|
| **Machine type** | e2-standard-8 (8 vCPU, 32GB RAM) | More CPU for parallel decompression |
| **Boot disk** | 50GB standard | OS only |
| **Data disk** | 5TB SSD pd-ssd | Must exceed 4TB torrent + working space |
| **Region** | us-central1-a | Same as BigQuery for fast loading |
| **Preemptible** | No | Torrent download needs stability |

**Estimated cost**: ~$0.27/hr × ~72-96 hours = **$20-30**

### GCS Staging
- Bucket: `gs://nomadkaraoke-data/spotify-audio-analysis/`
- Store extracted NDJSON files (gzipped)
- Delete after BigQuery load

## Implementation Steps

### Phase 1: VM Setup (30 min)

```bash
# Create VM with attached data disk
gcloud compute instances create spotify-etl-vm \
  --project=nomadkaraoke \
  --zone=us-central1-a \
  --machine-type=e2-standard-8 \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --create-disk=name=spotify-data,size=5000GB,type=pd-ssd,auto-delete=yes

# SSH into VM
gcloud compute ssh spotify-etl-vm --zone=us-central1-a

# Mount data disk
sudo mkfs.ext4 /dev/sdb
sudo mkdir /data
sudo mount /dev/sdb /data
sudo chown $USER:$USER /data

# Install dependencies
sudo apt-get update
sudo apt-get install -y python3-pip zstd transmission-cli aria2
pip3 install google-cloud-bigquery google-cloud-storage orjson tqdm
```

### Phase 2: Download Torrent (24-48 hours)

```bash
cd /data

# Start torrent download with aria2 (faster than transmission for large files)
aria2c --seed-time=0 \
  --max-concurrent-downloads=16 \
  --split=16 \
  --max-connection-per-server=16 \
  "magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f&dn=annas_archive_spotify_2025_07_audio_analysis.torrent&tr=udp://tracker.opentrackr.org:1337/announce"

# Alternative: transmission-cli
# transmission-cli -w /data "magnet:?xt=urn:btih:..."
```

### Phase 3: ETL Script

Create `scripts/spotify_audio_analysis_etl.py`:

```python
#!/usr/bin/env python3
"""
Extract track-level audio features from Spotify Audio Analysis dump.

Extracts only the summary fields we need for karaoke filtering,
ignoring the massive bars/beats/segments/tatums arrays.
"""

import gzip
import json
import os
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Iterator

import orjson
from google.cloud import bigquery, storage
from tqdm import tqdm

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET = "nomadkaraoke-data"
INPUT_DIR = Path("/data/annas_archive_spotify_2025_07_audio_analysis")
OUTPUT_DIR = Path("/data/output")
BATCH_SIZE = 100_000  # Rows per output file


def extract_track_summary(line: bytes) -> dict | None:
    """Extract only the track-level summary fields we need."""
    try:
        data = orjson.loads(line)

        # The spotify_id might be in meta or we need to parse from response
        spotify_id = data.get("meta", {}).get("spotify_track_id")
        if not spotify_id:
            # Some formats use different field names
            spotify_id = data.get("id") or data.get("track_id")

        if not spotify_id:
            return None

        track = data.get("track", {})

        return {
            "spotify_id": spotify_id,
            "tempo": track.get("tempo"),
            "tempo_confidence": track.get("tempo_confidence"),
            "time_signature": track.get("time_signature"),
            "time_signature_confidence": track.get("time_signature_confidence"),
            "key": track.get("key"),
            "key_confidence": track.get("key_confidence"),
            "mode": track.get("mode"),  # 0=minor, 1=major
            "mode_confidence": track.get("mode_confidence"),
            "loudness": track.get("loudness"),
            "duration": track.get("duration"),
            "end_of_fade_in": track.get("end_of_fade_in"),
            "start_of_fade_out": track.get("start_of_fade_out"),
        }
    except (orjson.JSONDecodeError, KeyError, TypeError):
        return None


def process_file(zst_path: Path) -> tuple[int, Path]:
    """Process a single .zst file and output NDJSON."""
    import subprocess

    output_path = OUTPUT_DIR / f"{zst_path.stem}.ndjson.gz"

    if output_path.exists():
        return (0, output_path)

    count = 0
    with gzip.open(output_path, "wt", encoding="utf-8") as out_f:
        # Decompress and stream through
        proc = subprocess.Popen(
            ["zstd", "-d", "-c", str(zst_path)],
            stdout=subprocess.PIPE,
        )

        for line in proc.stdout:
            record = extract_track_summary(line)
            if record:
                out_f.write(orjson.dumps(record).decode() + "\n")
                count += 1

        proc.wait()

    return (count, output_path)


def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    """Upload file to GCS and return URI."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path))
    return f"gs://{GCS_BUCKET}/{gcs_path}"


def load_to_bigquery(gcs_uris: list[str], table_id: str):
    """Load NDJSON files from GCS to BigQuery."""
    client = bigquery.Client(project=PROJECT_ID)

    schema = [
        bigquery.SchemaField("spotify_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("tempo", "FLOAT"),
        bigquery.SchemaField("tempo_confidence", "FLOAT"),
        bigquery.SchemaField("time_signature", "INTEGER"),
        bigquery.SchemaField("time_signature_confidence", "FLOAT"),
        bigquery.SchemaField("key", "INTEGER"),
        bigquery.SchemaField("key_confidence", "FLOAT"),
        bigquery.SchemaField("mode", "INTEGER"),
        bigquery.SchemaField("mode_confidence", "FLOAT"),
        bigquery.SchemaField("loudness", "FLOAT"),
        bigquery.SchemaField("duration", "FLOAT"),
        bigquery.SchemaField("end_of_fade_in", "FLOAT"),
        bigquery.SchemaField("start_of_fade_out", "FLOAT"),
    ]

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    # Load all files at once (BigQuery supports wildcards)
    load_job = client.load_table_from_uri(
        gcs_uris,
        table_id,
        job_config=job_config,
    )
    load_job.result()

    table = client.get_table(table_id)
    print(f"Loaded {table.num_rows:,} rows to {table_id}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Find all .zst files
    zst_files = sorted(INPUT_DIR.glob("*.json.zst"))
    print(f"Found {len(zst_files)} .zst files to process")

    # Process files in parallel
    gcs_uris = []
    total_rows = 0

    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_file, f): f for f in zst_files}

        for future in tqdm(as_completed(futures), total=len(futures)):
            count, output_path = future.result()
            total_rows += count

            if count > 0:
                # Upload to GCS
                gcs_path = f"spotify-audio-analysis/{output_path.name}"
                gcs_uri = upload_to_gcs(output_path, gcs_path)
                gcs_uris.append(gcs_uri)

                # Delete local file to save space
                output_path.unlink()

    print(f"\nExtracted {total_rows:,} tracks")
    print(f"Uploaded {len(gcs_uris)} files to GCS")

    # Load to BigQuery
    if gcs_uris:
        table_id = f"{PROJECT_ID}.{DATASET_ID}.spotify_audio_analysis"
        load_to_bigquery(gcs_uris, table_id)


if __name__ == "__main__":
    main()
```

### Phase 4: Run ETL (4-8 hours)

```bash
# Run the extraction script
python3 scripts/spotify_audio_analysis_etl.py

# Monitor progress
watch -n 60 'ls -la /data/output/*.ndjson.gz | wc -l'
```

### Phase 5: Verification

```sql
-- Check row count
SELECT COUNT(*) FROM `nomadkaraoke.karaoke_decide.spotify_audio_analysis`;

-- Check data quality
SELECT
  COUNT(*) as total,
  COUNT(tempo) as has_tempo,
  COUNT(key) as has_key,
  AVG(tempo) as avg_tempo,
  MIN(tempo) as min_tempo,
  MAX(tempo) as max_tempo
FROM `nomadkaraoke.karaoke_decide.spotify_audio_analysis`;

-- Check join potential with existing tracks
SELECT COUNT(DISTINCT a.spotify_id)
FROM `nomadkaraoke.karaoke_decide.spotify_audio_analysis` a
JOIN `nomadkaraoke.karaoke_decide.spotify_tracks` t
ON a.spotify_id = t.spotify_id;
```

### Phase 6: Cleanup

```bash
# Delete GCS staging data
gsutil -m rm -r gs://nomadkaraoke-data/spotify-audio-analysis/

# Delete VM
gcloud compute instances delete spotify-etl-vm --zone=us-central1-a
```

## Infrastructure Updates

### Pulumi: New BigQuery Table

Add to `infrastructure/__main__.py`:

```python
# Spotify audio analysis table
spotify_audio_analysis_table = gcp.bigquery.Table(
    "spotify-audio-analysis-table",
    dataset_id=bigquery_dataset.dataset_id,
    table_id="spotify_audio_analysis",
    project=project,
    schema='''[
        {"mode":"REQUIRED","name":"spotify_id","type":"STRING"},
        {"mode":"NULLABLE","name":"tempo","type":"FLOAT"},
        {"mode":"NULLABLE","name":"tempo_confidence","type":"FLOAT"},
        {"mode":"NULLABLE","name":"time_signature","type":"INTEGER"},
        {"mode":"NULLABLE","name":"time_signature_confidence","type":"FLOAT"},
        {"mode":"NULLABLE","name":"key","type":"INTEGER"},
        {"mode":"NULLABLE","name":"key_confidence","type":"FLOAT"},
        {"mode":"NULLABLE","name":"mode","type":"INTEGER"},
        {"mode":"NULLABLE","name":"mode_confidence","type":"FLOAT"},
        {"mode":"NULLABLE","name":"loudness","type":"FLOAT"},
        {"mode":"NULLABLE","name":"duration","type":"FLOAT"},
        {"mode":"NULLABLE","name":"end_of_fade_in","type":"FLOAT"},
        {"mode":"NULLABLE","name":"start_of_fade_out","type":"FLOAT"}
    ]''',
    opts=pulumi.ResourceOptions(protect=True),
)
```

### Combined View

Create a view that joins tracks with audio analysis:

```sql
CREATE OR REPLACE VIEW `nomadkaraoke.karaoke_decide.spotify_tracks_with_audio` AS
SELECT
  t.*,
  a.tempo,
  a.time_signature,
  a.key,
  a.mode,
  a.loudness,
  -- Derive energy score from loudness (normalize -60dB to 0dB → 0 to 1)
  GREATEST(0, LEAST(1, (a.loudness + 60) / 60)) as energy
FROM `nomadkaraoke.karaoke_decide.spotify_tracks` t
LEFT JOIN `nomadkaraoke.karaoke_decide.spotify_audio_analysis` a
ON t.spotify_id = a.spotify_id;
```

## Cost Estimate

| Item | Estimate |
|------|----------|
| GCE VM (e2-standard-8, 72-96 hrs) | $20-30 |
| SSD storage (5TB, 4 days) | $3-4 |
| Network egress (GCS upload ~10GB) | $0 (same region) |
| BigQuery storage (~8GB) | $0.16/month |
| BigQuery queries | Variable |
| **Total one-time** | **~$25-35** |

## Risk Mitigation

### Risks

1. **Torrent download fails** - Use aria2 with resume capability
2. **Disk fills up** - Process files incrementally, delete after upload
3. **VM preemption** - Use standard (not preemptible) VM
4. **spotify_id format mismatch** - Verify ID format before full run
5. **Memory issues** - Stream processing, don't load full files

### Validation Steps

1. Download a small sample (~1 file) first
2. Verify JSON structure and spotify_id extraction
3. Test BigQuery schema with sample data
4. Full run only after validation passes

## Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| VM Setup | 30 min | Manual |
| Torrent Download | 24-48 hrs | Depends on seeders |
| ETL Processing | 4-8 hrs | Parallelized |
| Verification | 30 min | SQL queries |
| Cleanup | 15 min | Delete VM + GCS |
| **Total** | **2-3 days** | |

## Future Enhancements

1. **Sections data** - Store section-level key/tempo for vocal range detection
2. **Audio features table** - If Anna's Archive releases a cleaner audio features dump
3. **Incremental updates** - Process new tracks as they're added
4. **Karaoke matching** - Create a joined view with karaoke catalog

## References

- [Anna's Archive Spotify Blog Post](https://annas-archive.li/blog/backing-up-spotify.html)
- [Spotify Audio Analysis API](https://developer.spotify.com/documentation/web-api/reference/get-audio-analysis)
- [Previous ETL: docs/archive/2024-12-30-data-foundation-and-frontend.md](../archive/2024-12-30-data-foundation-and-frontend.md)
- [Original Vision: docs/archive/2023-02-original-karaokehunt-roadmap.md](../archive/2023-02-original-karaokehunt-roadmap.md)
