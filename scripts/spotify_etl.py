#!/usr/bin/env python3
"""
Spotify ETL Script - Run on GCE VM to process Spotify dump to BigQuery.

This script:
1. Downloads compressed SQLite files from GCS
2. Decompresses them
3. Extracts relevant data
4. Loads to BigQuery

Usage:
    python3 spotify_etl.py
"""

import subprocess
import sqlite3
import os
from google.cloud import bigquery
from datetime import datetime

# Configuration
GCS_BUCKET = "karaoke-decide-data-nomadkaraoke"
PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
WORK_DIR = "/tmp/spotify_etl"

# Files to process
FILES = {
    "tracks": "spotify/spotify_clean.sqlite3.zst",
    "audio_features": "spotify/spotify_clean_audio_features.sqlite3.zst",
}


def run_cmd(cmd: str, description: str = ""):
    """Run shell command with logging."""
    print(f"[{datetime.now().isoformat()}] {description or cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result.stdout


def download_and_decompress(gcs_path: str, local_name: str) -> str:
    """Download from GCS and decompress with zstd."""
    compressed_path = f"{WORK_DIR}/{local_name}.zst"
    decompressed_path = f"{WORK_DIR}/{local_name}"

    if os.path.exists(decompressed_path):
        print(f"Already exists: {decompressed_path}")
        return decompressed_path

    # Download
    run_cmd(
        f"gsutil cp gs://{GCS_BUCKET}/{gcs_path} {compressed_path}",
        f"Downloading {gcs_path}..."
    )

    # Decompress
    run_cmd(
        f"zstd -d {compressed_path} -o {decompressed_path}",
        f"Decompressing {local_name}..."
    )

    # Clean up compressed file
    os.remove(compressed_path)

    return decompressed_path


def explore_schema(db_path: str):
    """Print the schema of a SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()

    print(f"\n=== Schema for {db_path} ===")
    for (table_name,) in tables:
        print(f"\nTable: {table_name}")
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  {col[1]} ({col[2]})")

        # Row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"  Rows: {count:,}")

    conn.close()


def load_tracks_to_bigquery(db_path: str, client: bigquery.Client):
    """Extract and load track data to BigQuery."""
    conn = sqlite3.connect(db_path)

    # First, explore the schema
    explore_schema(db_path)

    # Create temp table from query
    table_id = f"{PROJECT_ID}.{DATASET_ID}.spotify_tracks_raw"

    # Get data in batches and load to BigQuery
    query = """
        SELECT
            t.id as track_id,
            t.name as track_name,
            t.popularity,
            t.duration_ms,
            t.explicit,
            t.isrc,
            t.preview_url,
            a.name as artist_name,
            a.id as artist_id,
            a.followers as artist_followers,
            a.popularity as artist_popularity,
            al.name as album_name,
            al.release_date,
            al.release_date_precision
        FROM tracks t
        LEFT JOIN track_artists ta ON t.id = ta.track_id AND ta.position = 0
        LEFT JOIN artists a ON ta.artist_id = a.id
        LEFT JOIN albums al ON t.album_id = al.id
    """

    # Check what tables actually exist
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"Available tables: {tables}")

    # Adjust query based on available tables
    if 'tracks' in tables:
        cursor.execute("PRAGMA table_info(tracks)")
        track_cols = [col[1] for col in cursor.fetchall()]
        print(f"Track columns: {track_cols}")

    conn.close()
    print("Schema exploration complete. Need to adjust query based on actual schema.")


def load_audio_features_to_bigquery(db_path: str, client: bigquery.Client):
    """Extract and load audio features to BigQuery."""
    explore_schema(db_path)


def main():
    os.makedirs(WORK_DIR, exist_ok=True)

    # Initialize BigQuery client
    client = bigquery.Client(project=PROJECT_ID)

    # Process tracks database
    print("\n" + "="*60)
    print("Processing Spotify Tracks Database")
    print("="*60)

    tracks_db = download_and_decompress(FILES["tracks"], "spotify_clean.sqlite3")
    load_tracks_to_bigquery(tracks_db, client)

    # Process audio features database
    print("\n" + "="*60)
    print("Processing Spotify Audio Features Database")
    print("="*60)

    features_db = download_and_decompress(FILES["audio_features"], "spotify_clean_audio_features.sqlite3")
    load_audio_features_to_bigquery(features_db, client)


if __name__ == "__main__":
    main()
