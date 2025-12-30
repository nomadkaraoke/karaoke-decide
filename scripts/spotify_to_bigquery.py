#!/usr/bin/env python3
"""
Export Spotify tracks from SQLite to BigQuery.
Exports tracks with primary artist name, ISRC, popularity for karaoke matching.
"""

import gzip
import json
import sqlite3
import subprocess
from pathlib import Path

DB_PATH = "/tmp/annas_archive_spotify_2025_07_metadata/spotify_clean.sqlite3"
OUTPUT_DIR = Path("/tmp/spotify_export")
GCS_BUCKET = "gs://nomadkaraoke-data/spotify"
PROJECT = "nomadkaraoke"
DATASET = "karaoke_decide"
TABLE = "spotify_tracks"
BATCH_SIZE = 500000  # 500K rows per file


def export_tracks():
    """Export tracks with artist info to NDJSON files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query to get tracks with primary artist name
    query = """
    SELECT
        t.id as spotify_id,
        t.name as title,
        t.external_id_isrc as isrc,
        t.popularity,
        t.duration_ms,
        t.explicit,
        a.name as artist_name,
        a.id as artist_spotify_id,
        a.popularity as artist_popularity,
        a.followers_total as artist_followers
    FROM tracks t
    JOIN track_artists ta ON t.rowid = ta.track_rowid
    JOIN artists a ON ta.artist_rowid = a.rowid
    GROUP BY t.id
    HAVING ta.artist_rowid = MIN(ta.artist_rowid)
    """

    print("Starting export...")
    cursor.execute(query)

    file_num = 0
    row_count = 0
    total_count = 0
    current_file = None

    for row in cursor:
        if row_count == 0:
            file_path = OUTPUT_DIR / f"tracks_{file_num:04d}.ndjson.gz"
            current_file = gzip.open(file_path, "wt", encoding="utf-8")
            print(f"Writing {file_path}...")

        record = {
            "spotify_id": row["spotify_id"],
            "title": row["title"],
            "isrc": row["isrc"],
            "popularity": row["popularity"],
            "duration_ms": row["duration_ms"],
            "explicit": bool(row["explicit"]),
            "artist_name": row["artist_name"],
            "artist_spotify_id": row["artist_spotify_id"],
            "artist_popularity": row["artist_popularity"],
            "artist_followers": row["artist_followers"],
        }
        current_file.write(json.dumps(record) + "\n")

        row_count += 1
        total_count += 1

        if row_count >= BATCH_SIZE:
            current_file.close()
            file_num += 1
            row_count = 0
            print(f"  Exported {total_count:,} tracks so far...")

    if current_file and row_count > 0:
        current_file.close()

    conn.close()
    print(f"Export complete: {total_count:,} tracks in {file_num + 1} files")
    return file_num + 1


def upload_to_gcs(num_files):
    """Upload NDJSON files to GCS."""
    print(f"Uploading {num_files} files to {GCS_BUCKET}...")
    subprocess.run(["gsutil", "-m", "cp", str(OUTPUT_DIR / "tracks_*.ndjson.gz"), GCS_BUCKET + "/"], check=True)
    print("Upload complete")


def load_to_bigquery():
    """Load data from GCS to BigQuery."""
    print(f"Loading to BigQuery table {PROJECT}.{DATASET}.{TABLE}...")

    # Create or replace table
    schema = """
    spotify_id:STRING,
    title:STRING,
    isrc:STRING,
    popularity:INTEGER,
    duration_ms:INTEGER,
    explicit:BOOLEAN,
    artist_name:STRING,
    artist_spotify_id:STRING,
    artist_popularity:INTEGER,
    artist_followers:INTEGER
    """

    subprocess.run(
        [
            "bq",
            "load",
            "--replace",
            "--source_format=NEWLINE_DELIMITED_JSON",
            f"{PROJECT}:{DATASET}.{TABLE}",
            f"{GCS_BUCKET}/tracks_*.ndjson.gz",
            schema.replace("\n", ",").replace(" ", ""),
        ],
        check=True,
    )
    print("BigQuery load complete")


if __name__ == "__main__":
    print("=== Spotify to BigQuery ETL ===")
    num_files = export_tracks()
    upload_to_gcs(num_files)
    load_to_bigquery()
    print("=== Done ===")
