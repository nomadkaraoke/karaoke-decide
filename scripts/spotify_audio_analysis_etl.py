#!/usr/bin/env python3
"""
Spotify Audio Analysis ETL Script - Extract track-level features from 4TB dump.

This script extracts only the summary fields we need for karaoke filtering,
ignoring the massive bars/beats/segments/tatums arrays that account for ~99%
of the raw data.

Usage:
    # Run on GCE VM after torrent download
    python3 spotify_audio_analysis_etl.py

    # With custom paths
    python3 spotify_audio_analysis_etl.py --input /data/torrent --output /data/output

See docs/plans/2025-01-spotify-audio-analysis-etl.md for full setup instructions.
"""

import argparse
import gzip
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import orjson
except ImportError:
    print("Installing orjson...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "orjson"])
    import orjson

try:
    from google.cloud import bigquery, storage
except ImportError:
    print("Installing google-cloud libraries...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-cloud-bigquery", "google-cloud-storage"])
    from google.cloud import bigquery, storage

try:
    from tqdm import tqdm
except ImportError:
    print("Installing tqdm...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm"])
    from tqdm import tqdm

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
TABLE_ID = "spotify_audio_analysis"
GCS_BUCKET = "nomadkaraoke-data"
GCS_PREFIX = "spotify-audio-analysis"

# Default paths (override with args)
DEFAULT_INPUT_DIR = Path("/data/annas_archive_spotify_2025_07_audio_analysis")
DEFAULT_OUTPUT_DIR = Path("/data/output")


def log(msg: str) -> None:
    """Print timestamped log message."""
    print(f"[{datetime.now().isoformat()}] {msg}")


def extract_track_summary(line: bytes) -> dict | None:
    """
    Extract only the track-level summary fields we need.

    The raw audio analysis JSON contains:
    - meta: analyzer metadata
    - track: summary (tempo, key, mode, loudness, etc.) <- WE WANT THIS
    - bars: hundreds of bar markers <- SKIP
    - beats: thousands of beat markers <- SKIP
    - sections: 10-20 sections <- MAYBE LATER
    - segments: thousands of pitch/timbre vectors <- SKIP
    - tatums: thousands of tatum markers <- SKIP
    """
    try:
        data = orjson.loads(line)

        # Extract spotify_id from various possible locations
        spotify_id = None

        # Try meta.spotify_track_id first
        meta = data.get("meta", {})
        if isinstance(meta, dict):
            spotify_id = meta.get("spotify_track_id") or meta.get("track_id")

        # Try top-level id
        if not spotify_id:
            spotify_id = data.get("id") or data.get("track_id") or data.get("spotify_id")

        # Try extracting from input_process if present
        if not spotify_id and "meta" in data:
            input_proc = meta.get("input_process", "")
            if "spotify:" in input_proc:
                # Extract ID from spotify:track:XXXXX format
                parts = input_proc.split("spotify:track:")
                if len(parts) > 1:
                    spotify_id = parts[1].split()[0]

        if not spotify_id:
            return None

        track = data.get("track", {})
        if not isinstance(track, dict):
            return None

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
    except (orjson.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def process_file(args: tuple) -> tuple[int, int, Path | None]:
    """
    Process a single .zst file and output compressed NDJSON.

    Returns: (processed_count, error_count, output_path or None)
    """
    zst_path, output_dir = args
    output_path = output_dir / f"{zst_path.stem}.ndjson.gz"

    # Skip if already processed
    if output_path.exists():
        return (0, 0, output_path)

    processed = 0
    errors = 0

    try:
        with gzip.open(output_path, "wt", encoding="utf-8") as out_f:
            # Decompress and stream through zstd
            proc = subprocess.Popen(
                ["zstd", "-d", "-c", str(zst_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            for line in proc.stdout:
                if not line.strip():
                    continue

                record = extract_track_summary(line)
                if record:
                    out_f.write(orjson.dumps(record).decode() + "\n")
                    processed += 1
                else:
                    errors += 1

            proc.wait()

        return (processed, errors, output_path)

    except Exception as e:
        # Clean up partial output
        if output_path.exists():
            output_path.unlink()
        log(f"Error processing {zst_path.name}: {e}")
        return (0, errors + 1, None)


def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    """Upload file to GCS and return URI."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    log(f"Uploading {local_path.name} to GCS...")
    blob.upload_from_filename(str(local_path))

    return f"gs://{GCS_BUCKET}/{gcs_path}"


def load_to_bigquery(gcs_uri_pattern: str) -> int:
    """
    Load NDJSON files from GCS to BigQuery.

    Returns: Number of rows loaded
    """
    client = bigquery.Client(project=PROJECT_ID)
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

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
        ignore_unknown_values=True,
    )

    log(f"Loading {gcs_uri_pattern} to BigQuery...")
    load_job = client.load_table_from_uri(
        gcs_uri_pattern,
        table_ref,
        job_config=job_config,
    )
    load_job.result()  # Wait for completion

    table = client.get_table(table_ref)
    log(f"Loaded {table.num_rows:,} rows to {table_ref}")

    return table.num_rows


def verify_sample(input_dir: Path) -> bool:
    """
    Verify we can parse the data format with a sample file.

    Returns True if sample parsing succeeds.
    """
    log("Verifying data format with sample...")

    zst_files = sorted(input_dir.glob("*.json.zst"))[:1]
    if not zst_files:
        log("ERROR: No .json.zst files found!")
        return False

    sample_file = zst_files[0]
    log(f"Testing with {sample_file.name}...")

    # Read first 100 lines
    proc = subprocess.Popen(
        ["zstd", "-d", "-c", str(sample_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    parsed = 0
    failed = 0
    sample_record = None

    for i, line in enumerate(proc.stdout):
        if i >= 100:
            break
        if not line.strip():
            continue

        record = extract_track_summary(line)
        if record:
            parsed += 1
            if not sample_record:
                sample_record = record
        else:
            failed += 1

    proc.terminate()

    if parsed == 0:
        log("ERROR: Could not parse any records!")
        log("Raw sample line:")
        proc2 = subprocess.Popen(
            ["zstd", "-d", "-c", str(sample_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        for line in proc2.stdout:
            if line.strip():
                log(line[:500].decode(errors="replace"))
                break
        proc2.terminate()
        return False

    log(f"Sample parsing: {parsed} succeeded, {failed} failed")
    if sample_record:
        log(f"Sample record: {sample_record}")

    return True


def main():
    parser = argparse.ArgumentParser(description="Spotify Audio Analysis ETL")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing .json.zst files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for intermediate NDJSON files",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip sample verification",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip GCS upload (for testing)",
    )
    parser.add_argument(
        "--skip-bigquery",
        action="store_true",
        help="Skip BigQuery load (for testing)",
    )
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="Don't delete local files after upload",
    )

    args = parser.parse_args()

    log("=" * 60)
    log("Spotify Audio Analysis ETL")
    log("=" * 60)

    # Validate input directory
    if not args.input.exists():
        log(f"ERROR: Input directory not found: {args.input}")
        sys.exit(1)

    # Create output directory
    args.output.mkdir(parents=True, exist_ok=True)

    # Verify data format
    if not args.skip_verify:
        if not verify_sample(args.input):
            log("Sample verification failed. Check data format.")
            sys.exit(1)

    # Find all .zst files
    zst_files = sorted(args.input.glob("*.json.zst"))
    log(f"Found {len(zst_files)} .json.zst files to process")

    if not zst_files:
        log("No files to process!")
        sys.exit(1)

    # Process files in parallel
    total_processed = 0
    total_errors = 0
    output_files = []

    log(f"\nProcessing with {args.workers} workers...")

    work_items = [(f, args.output) for f in zst_files]

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_file, item): item[0] for item in work_items}

        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing"):
            zst_file = futures[future]
            try:
                processed, errors, output_path = future.result()
                total_processed += processed
                total_errors += errors

                if output_path and output_path.exists():
                    output_files.append(output_path)

            except Exception as e:
                log(f"Error with {zst_file.name}: {e}")
                total_errors += 1

    log("\nExtraction complete:")
    log(f"  Total records: {total_processed:,}")
    log(f"  Total errors: {total_errors:,}")
    log(f"  Output files: {len(output_files)}")

    if not output_files:
        log("No output files created!")
        sys.exit(1)

    # Upload to GCS
    if not args.skip_upload:
        log("\nUploading to GCS...")

        gcs_uris = []
        for output_file in tqdm(output_files, desc="Uploading"):
            gcs_path = f"{GCS_PREFIX}/{output_file.name}"
            uri = upload_to_gcs(output_file, gcs_path)
            gcs_uris.append(uri)

            # Delete local file to save space
            if not args.keep_local:
                output_file.unlink()

        log(f"Uploaded {len(gcs_uris)} files to GCS")

        # Load to BigQuery
        if not args.skip_bigquery:
            log("\nLoading to BigQuery...")
            gcs_pattern = f"gs://{GCS_BUCKET}/{GCS_PREFIX}/*.ndjson.gz"
            rows = load_to_bigquery(gcs_pattern)

            log("\nETL Complete!")
            log(f"  BigQuery rows: {rows:,}")

    log("\n" + "=" * 60)
    log("Done!")
    log("=" * 60)


if __name__ == "__main__":
    main()
