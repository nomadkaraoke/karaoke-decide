#!/usr/bin/env python3
"""
Full Spotify Audio Analysis ETL - Extracts track summaries AND sections.

This script processes the 4TB Spotify Audio Analysis torrent and extracts:
1. Track-level summary data (spotify_audio_analysis_tracks table)
2. Section-level data (spotify_audio_analysis_sections table)

Unlike the simpler spotify_audio_features table (from metadata dump), this extracts:
- Confidence scores for tempo, key, mode, time_signature
- Fade markers (end_of_fade_in, start_of_fade_out)
- Section-by-section breakdown of tempo/key/mode changes

Usage:
    python3 spotify_audio_analysis_full_etl.py [--dry-run] [--sample N]
"""

import argparse
import gzip
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET = "nomadkaraoke-data"
INPUT_DIR = Path("/data/torrents/spotify-audio-analysis-2025-07/annas_archive_spotify_2025_07_audio_analysis")
OUTPUT_DIR = Path("/data/output")
TRACKS_OUTPUT_DIR = OUTPUT_DIR / "tracks"
SECTIONS_OUTPUT_DIR = OUTPUT_DIR / "sections"


def extract_track_and_sections(line: bytes) -> tuple[dict | None, list[dict]]:
    """Extract track summary and sections from a single JSON line."""
    try:
        # Use standard json for compatibility
        data = json.loads(line.decode("utf-8"))

        # Get spotify_id from meta
        meta = data.get("meta", {})
        spotify_id = meta.get("track_id")
        if not spotify_id:
            return None, []

        track_data = data.get("track", {})

        # Track-level summary
        track_record = {
            "spotify_id": spotify_id,
            "duration": track_data.get("duration"),
            "tempo": track_data.get("tempo"),
            "tempo_confidence": track_data.get("tempo_confidence"),
            "time_signature": track_data.get("time_signature"),
            "time_signature_confidence": track_data.get("time_signature_confidence"),
            "key": track_data.get("key"),
            "key_confidence": track_data.get("key_confidence"),
            "mode": track_data.get("mode"),
            "mode_confidence": track_data.get("mode_confidence"),
            "loudness": track_data.get("loudness"),
            "end_of_fade_in": track_data.get("end_of_fade_in"),
            "start_of_fade_out": track_data.get("start_of_fade_out"),
            "num_samples": track_data.get("num_samples"),
            "analysis_sample_rate": track_data.get("analysis_sample_rate"),
            # Meta fields
            "analyzer_version": meta.get("analyzer_version"),
            "analysis_time": meta.get("analysis_time"),
        }

        # Section-level data
        sections = data.get("sections", [])
        section_records = []
        for i, section in enumerate(sections):
            section_record = {
                "spotify_id": spotify_id,
                "section_index": i,
                "start": section.get("start"),
                "duration": section.get("duration"),
                "confidence": section.get("confidence"),
                "loudness": section.get("loudness"),
                "tempo": section.get("tempo"),
                "tempo_confidence": section.get("tempo_confidence"),
                "key": section.get("key"),
                "key_confidence": section.get("key_confidence"),
                "mode": section.get("mode"),
                "mode_confidence": section.get("mode_confidence"),
                "time_signature": section.get("time_signature"),
                "time_signature_confidence": section.get("time_signature_confidence"),
            }
            section_records.append(section_record)

        return track_record, section_records

    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
        return None, []


def process_file(zst_path: Path, dry_run: bool = False) -> tuple[int, int, Path | None, Path | None]:
    """Process a single .zst file and output NDJSON for tracks and sections."""
    tracks_output = TRACKS_OUTPUT_DIR / f"{zst_path.stem}_tracks.ndjson.gz"
    sections_output = SECTIONS_OUTPUT_DIR / f"{zst_path.stem}_sections.ndjson.gz"

    # Skip if already processed
    if tracks_output.exists() and sections_output.exists():
        return 0, 0, tracks_output, sections_output

    if dry_run:
        return 0, 0, None, None

    track_count = 0
    section_count = 0

    try:
        with (
            gzip.open(tracks_output, "wt", encoding="utf-8") as tracks_f,
            gzip.open(sections_output, "wt", encoding="utf-8") as sections_f,
        ):
            # Decompress and stream through
            proc = subprocess.Popen(
                ["zstd", "-d", "-c", str(zst_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            for line in proc.stdout:
                track_record, section_records = extract_track_and_sections(line)

                if track_record:
                    tracks_f.write(json.dumps(track_record) + "\n")
                    track_count += 1

                for section_record in section_records:
                    sections_f.write(json.dumps(section_record) + "\n")
                    section_count += 1

            proc.wait()

    except Exception as e:
        print(f"Error processing {zst_path}: {e}", file=sys.stderr)
        # Clean up partial files
        if tracks_output.exists():
            tracks_output.unlink()
        if sections_output.exists():
            sections_output.unlink()
        return 0, 0, None, None

    return track_count, section_count, tracks_output, sections_output


def upload_to_gcs(local_path: Path, gcs_path: str) -> str:
    """Upload file to GCS and return URI."""
    from google.cloud import storage

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path))
    return f"gs://{GCS_BUCKET}/{gcs_path}"


def load_to_bigquery(gcs_uris: list[str], table_id: str, schema: list[dict]):
    """Load NDJSON files from GCS to BigQuery."""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT_ID)

    bq_schema = [
        bigquery.SchemaField(field["name"], field["type"], mode=field.get("mode", "NULLABLE")) for field in schema
    ]

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=bq_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    load_job = client.load_table_from_uri(
        gcs_uris,
        table_id,
        job_config=job_config,
    )
    load_job.result()

    table = client.get_table(table_id)
    print(f"Loaded {table.num_rows:,} rows to {table_id}")


# BigQuery schemas
TRACKS_SCHEMA = [
    {"name": "spotify_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "duration", "type": "FLOAT"},
    {"name": "tempo", "type": "FLOAT"},
    {"name": "tempo_confidence", "type": "FLOAT"},
    {"name": "time_signature", "type": "INTEGER"},
    {"name": "time_signature_confidence", "type": "FLOAT"},
    {"name": "key", "type": "INTEGER"},
    {"name": "key_confidence", "type": "FLOAT"},
    {"name": "mode", "type": "INTEGER"},
    {"name": "mode_confidence", "type": "FLOAT"},
    {"name": "loudness", "type": "FLOAT"},
    {"name": "end_of_fade_in", "type": "FLOAT"},
    {"name": "start_of_fade_out", "type": "FLOAT"},
    {"name": "num_samples", "type": "INTEGER"},
    {"name": "analysis_sample_rate", "type": "INTEGER"},
    {"name": "analyzer_version", "type": "STRING"},
    {"name": "analysis_time", "type": "FLOAT"},
]

SECTIONS_SCHEMA = [
    {"name": "spotify_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "section_index", "type": "INTEGER", "mode": "REQUIRED"},
    {"name": "start", "type": "FLOAT"},
    {"name": "duration", "type": "FLOAT"},
    {"name": "confidence", "type": "FLOAT"},
    {"name": "loudness", "type": "FLOAT"},
    {"name": "tempo", "type": "FLOAT"},
    {"name": "tempo_confidence", "type": "FLOAT"},
    {"name": "key", "type": "INTEGER"},
    {"name": "key_confidence", "type": "FLOAT"},
    {"name": "mode", "type": "INTEGER"},
    {"name": "mode_confidence", "type": "FLOAT"},
    {"name": "time_signature", "type": "INTEGER"},
    {"name": "time_signature_confidence", "type": "FLOAT"},
]


def main():
    parser = argparse.ArgumentParser(description="Full Spotify Audio Analysis ETL")
    parser.add_argument("--dry-run", action="store_true", help="Don't process, just show what would be done")
    parser.add_argument("--sample", type=int, help="Process only N files for testing")
    parser.add_argument("--skip-upload", action="store_true", help="Skip GCS upload and BigQuery load")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    args = parser.parse_args()

    # Create output directories
    TRACKS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SECTIONS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Find all .zst files
    zst_files = sorted(INPUT_DIR.glob("*.jsonl.zst"))
    if args.sample:
        zst_files = zst_files[: args.sample]

    print(f"Found {len(zst_files)} .zst files to process")

    if args.dry_run:
        print("Dry run - no files will be processed")
        return

    # Process files in parallel
    total_tracks = 0
    total_sections = 0
    tracks_outputs = []
    sections_outputs = []

    try:
        from tqdm import tqdm

        progress = tqdm(total=len(zst_files), desc="Processing files")
    except ImportError:
        progress = None
        print("Install tqdm for progress bar: pip install tqdm")

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_file, f, args.dry_run): f for f in zst_files}

        for future in as_completed(futures):
            track_count, section_count, tracks_out, sections_out = future.result()
            total_tracks += track_count
            total_sections += section_count

            if tracks_out:
                tracks_outputs.append(tracks_out)
            if sections_out:
                sections_outputs.append(sections_out)

            if progress:
                progress.update(1)
                progress.set_postfix(tracks=total_tracks, sections=total_sections)

    if progress:
        progress.close()

    print(f"\nExtracted {total_tracks:,} tracks and {total_sections:,} sections")

    if args.skip_upload:
        print("Skipping GCS upload and BigQuery load (--skip-upload)")
        return

    # Upload to GCS and load to BigQuery
    print("\nUploading tracks to GCS...")
    tracks_gcs_uris = []
    for output_path in tracks_outputs:
        gcs_path = f"spotify-audio-analysis-etl/tracks/{output_path.name}"
        gcs_uri = upload_to_gcs(output_path, gcs_path)
        tracks_gcs_uris.append(gcs_uri)
        # Delete local file to save space
        output_path.unlink()

    print("\nUploading sections to GCS...")
    sections_gcs_uris = []
    for output_path in sections_outputs:
        gcs_path = f"spotify-audio-analysis-etl/sections/{output_path.name}"
        gcs_uri = upload_to_gcs(output_path, gcs_path)
        sections_gcs_uris.append(gcs_uri)
        output_path.unlink()

    # Load to BigQuery
    print("\nLoading tracks to BigQuery...")
    if tracks_gcs_uris:
        load_to_bigquery(
            tracks_gcs_uris,
            f"{PROJECT_ID}.{DATASET_ID}.spotify_audio_analysis_tracks",
            TRACKS_SCHEMA,
        )

    print("\nLoading sections to BigQuery...")
    if sections_gcs_uris:
        load_to_bigquery(
            sections_gcs_uris,
            f"{PROJECT_ID}.{DATASET_ID}.spotify_audio_analysis_sections",
            SECTIONS_SCHEMA,
        )

    print("\nETL complete!")


if __name__ == "__main__":
    main()
