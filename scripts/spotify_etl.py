#!/usr/bin/env python3
"""
Spotify ETL Script - Run on GCE VM to process Spotify dump to BigQuery.

This script:
1. Downloads compressed SQLite files from GCS (if not local)
2. Decompresses them
3. Extracts relevant data to NDJSON
4. Uploads to GCS
5. Loads to BigQuery

Usage:
    python3 spotify_etl.py
"""

import subprocess
import sqlite3
import os
import json
import gzip
from datetime import datetime
from google.cloud import bigquery, storage

# Configuration
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
GCS_BUCKET = "karaoke-decide-data-nomadkaraoke"
WORK_DIR = "/tmp/annas_archive_spotify_2025_07_metadata"

# Source files
FILES = {
    "tracks": {
        "zst": "spotify_clean.sqlite3.zst",
        "sqlite": "spotify_clean.sqlite3",
    },
    "audio": {
        "zst": "spotify_clean_audio_features.sqlite3.zst",
        "sqlite": "spotify_clean_audio_features.sqlite3",
    },
}


def run_cmd(cmd: str, description: str = ""):
    """Run shell command with logging."""
    print(f"[{datetime.now().isoformat()}] {description or cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result.stdout


def decompress(zst_path: str, out_path: str):
    """Decompress a zstd file."""
    if os.path.exists(out_path):
        print(f"Already decompressed: {out_path}")
        return
    run_cmd(f"zstd -d {zst_path} -o {out_path}", f"Decompressing {zst_path}...")


def explore_schema(db_path: str) -> dict:
    """Get the schema of a SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    schema = {}
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]

    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [(col[1], col[2]) for col in cursor.fetchall()]
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        schema[table] = {"columns": columns, "count": count}

    conn.close()
    return schema


def extract_tracks_to_ndjson(db_path: str, output_path: str, batch_size: int = 100000):
    """Extract track data to NDJSON file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get schema first
    print("Exploring schema...")
    schema = explore_schema(db_path)
    print(f"Tables found: {list(schema.keys())}")
    for table, info in schema.items():
        print(f"  {table}: {info['count']:,} rows")

    # Determine the actual query based on available tables
    if 'tracks' in schema:
        # Anna's Archive format
        query = """
            SELECT
                t.id as spotify_track_id,
                t.name as track_name,
                t.popularity,
                t.duration_ms,
                t.explicit,
                t.isrc,
                a.name as artist_name,
                a.id as artist_id,
                a.followers as artist_followers,
                a.popularity as artist_popularity
            FROM tracks t
            LEFT JOIN track_artists ta ON t.id = ta.track_id AND ta.position = 0
            LEFT JOIN artists a ON ta.artist_id = a.id
        """
    else:
        raise ValueError(f"Unknown schema format. Tables: {list(schema.keys())}")

    print(f"Extracting to {output_path}...")
    with gzip.open(output_path, 'wt', encoding='utf-8') as f:
        offset = 0
        total = 0
        while True:
            cursor.execute(f"{query} LIMIT {batch_size} OFFSET {offset}")
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                record = dict(row)
                # Convert any non-JSON-serializable types
                for k, v in record.items():
                    if isinstance(v, bytes):
                        record[k] = v.decode('utf-8', errors='replace')
                f.write(json.dumps(record) + '\n')

            total += len(rows)
            offset += batch_size
            print(f"  Extracted {total:,} rows...")

    conn.close()
    print(f"Extracted {total:,} total rows to {output_path}")
    return total


def extract_audio_features_to_ndjson(db_path: str, output_path: str, batch_size: int = 100000):
    """Extract audio features to NDJSON file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get schema first
    print("Exploring schema...")
    schema = explore_schema(db_path)
    print(f"Tables found: {list(schema.keys())}")
    for table, info in schema.items():
        print(f"  {table}: {info['count']:,} rows")

    # Find the audio features table
    audio_table = None
    for table in schema.keys():
        if 'audio' in table.lower() or 'feature' in table.lower():
            audio_table = table
            break

    if not audio_table:
        audio_table = list(schema.keys())[0]  # Use first table

    print(f"Using table: {audio_table}")
    columns = [col[0] for col in schema[audio_table]['columns']]
    print(f"Columns: {columns}")

    query = f"SELECT * FROM {audio_table}"

    print(f"Extracting to {output_path}...")
    with gzip.open(output_path, 'wt', encoding='utf-8') as f:
        offset = 0
        total = 0
        while True:
            cursor.execute(f"{query} LIMIT {batch_size} OFFSET {offset}")
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                record = dict(row)
                for k, v in record.items():
                    if isinstance(v, bytes):
                        record[k] = v.decode('utf-8', errors='replace')
                f.write(json.dumps(record) + '\n')

            total += len(rows)
            offset += batch_size
            print(f"  Extracted {total:,} rows...")

    conn.close()
    print(f"Extracted {total:,} total rows to {output_path}")
    return total


def upload_to_gcs(local_path: str, gcs_path: str):
    """Upload a file to GCS."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    print(f"Uploading {local_path} to gs://{GCS_BUCKET}/{gcs_path}...")
    blob.upload_from_filename(local_path)
    print(f"Upload complete: gs://{GCS_BUCKET}/{gcs_path}")


def load_to_bigquery(gcs_uri: str, table_id: str, schema: list = None):
    """Load NDJSON from GCS to BigQuery."""
    client = bigquery.Client(project=PROJECT_ID)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True if not schema else False,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    if schema:
        job_config.schema = schema

    print(f"Loading {gcs_uri} to {table_id}...")
    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()  # Wait for completion

    table = client.get_table(table_id)
    print(f"Loaded {table.num_rows:,} rows to {table_id}")


def main():
    """Main ETL process."""
    print("=" * 60)
    print("Spotify ETL to BigQuery")
    print("=" * 60)

    # Check if files exist
    for name, files in FILES.items():
        zst_path = os.path.join(WORK_DIR, files["zst"])
        if not os.path.exists(zst_path):
            print(f"ERROR: {zst_path} not found!")
            print("Make sure the torrent download is complete.")
            return

    # Process tracks
    print("\n" + "=" * 60)
    print("Processing Spotify Tracks")
    print("=" * 60)

    tracks_zst = os.path.join(WORK_DIR, FILES["tracks"]["zst"])
    tracks_db = os.path.join(WORK_DIR, FILES["tracks"]["sqlite"])
    tracks_ndjson = os.path.join(WORK_DIR, "spotify_tracks.ndjson.gz")

    decompress(tracks_zst, tracks_db)
    extract_tracks_to_ndjson(tracks_db, tracks_ndjson)
    upload_to_gcs(tracks_ndjson, "spotify/spotify_tracks.ndjson.gz")
    load_to_bigquery(
        f"gs://{GCS_BUCKET}/spotify/spotify_tracks.ndjson.gz",
        f"{PROJECT_ID}.{DATASET_ID}.spotify_tracks_raw"
    )

    # Process audio features
    print("\n" + "=" * 60)
    print("Processing Spotify Audio Features")
    print("=" * 60)

    audio_zst = os.path.join(WORK_DIR, FILES["audio"]["zst"])
    audio_db = os.path.join(WORK_DIR, FILES["audio"]["sqlite"])
    audio_ndjson = os.path.join(WORK_DIR, "spotify_audio_features.ndjson.gz")

    decompress(audio_zst, audio_db)
    extract_audio_features_to_ndjson(audio_db, audio_ndjson)
    upload_to_gcs(audio_ndjson, "spotify/spotify_audio_features.ndjson.gz")
    load_to_bigquery(
        f"gs://{GCS_BUCKET}/spotify/spotify_audio_features.ndjson.gz",
        f"{PROJECT_ID}.{DATASET_ID}.spotify_audio_features_raw"
    )

    print("\n" + "=" * 60)
    print("ETL Complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
