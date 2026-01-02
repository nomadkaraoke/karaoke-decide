#!/usr/bin/env python3
"""
Complete Spotify Metadata ETL to BigQuery.

Extracts ALL useful metadata from the Anna's Archive Spotify dump:
- Artists with genres
- Albums with release dates
- Tracks with full metadata
- Track-artist relationships
- Audio features (danceability, energy, etc.)

Run this on a GCE VM with:
1. The SQLite dump files downloaded and decompressed
2. gcloud CLI authenticated
3. gsutil and bq CLI tools available

Usage:
    python3 spotify_complete_etl.py [--explore] [--table TABLE_NAME]

Options:
    --explore   Only explore and print schema, don't extract
    --table     Extract only a specific table (e.g., spotify_artist_genres)
"""

import argparse
import gzip
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Configuration
PROJECT = "nomadkaraoke"
DATASET = "karaoke_decide"
GCS_BUCKET = "gs://nomadkaraoke-data/spotify_metadata"
WORK_DIR = Path("/data/spotify_sqlite")  # Decompressed SQLite files go here
OUTPUT_DIR = Path("/data/spotify_export")  # NDJSON export goes here
SOURCE_DIR = Path("/data/annas_archive_spotify_2025_07_metadata")  # Downloaded .zst files
BATCH_SIZE = 500_000  # Rows per NDJSON file


@dataclass
class TableExtraction:
    """Configuration for extracting a table."""

    sqlite_db: str  # SQLite database filename
    query: str  # SQL query to extract data
    bigquery_table: str  # Target BigQuery table name
    schema: str  # BigQuery schema definition
    description: str  # Human-readable description


# All tables to extract
EXTRACTIONS: list[TableExtraction] = [
    # === Artists ===
    TableExtraction(
        sqlite_db="spotify_clean.sqlite3",
        query="""
            SELECT
                id as artist_id,
                name as artist_name,
                followers_total,
                popularity
            FROM artists
        """,
        bigquery_table="spotify_artists",
        schema="artist_id:STRING,artist_name:STRING,followers_total:INTEGER,popularity:INTEGER",
        description="Spotify artist metadata (~500K artists)",
    ),
    # === Artist Genres ===
    TableExtraction(
        sqlite_db="spotify_clean.sqlite3",
        query="""
            SELECT
                a.id as artist_id,
                g.genre
            FROM artists a
            JOIN artist_genres g ON a.rowid = g.artist_rowid
        """,
        bigquery_table="spotify_artist_genres",
        schema="artist_id:STRING,genre:STRING",
        description="Artist genre associations (~2-3M rows, artists can have multiple genres)",
    ),
    # === Albums ===
    TableExtraction(
        sqlite_db="spotify_clean.sqlite3",
        query="""
            SELECT
                id as album_id,
                name as album_name,
                album_type,
                release_date,
                release_date_precision,
                label,
                popularity,
                total_tracks
            FROM albums
        """,
        bigquery_table="spotify_albums",
        schema="album_id:STRING,album_name:STRING,album_type:STRING,release_date:STRING,release_date_precision:STRING,label:STRING,popularity:INTEGER,total_tracks:INTEGER",
        description="Album metadata with release dates (~50M albums)",
    ),
    # === Tracks (Full) ===
    TableExtraction(
        sqlite_db="spotify_clean.sqlite3",
        query="""
            SELECT
                t.id as track_id,
                t.name as track_name,
                al.id as album_id,
                t.popularity,
                t.duration_ms,
                t.explicit,
                t.external_id_isrc as isrc,
                t.track_number,
                t.disc_number
            FROM tracks t
            LEFT JOIN albums al ON t.album_rowid = al.rowid
        """,
        bigquery_table="spotify_tracks_full",
        schema="track_id:STRING,track_name:STRING,album_id:STRING,popularity:INTEGER,duration_ms:INTEGER,explicit:BOOLEAN,isrc:STRING,track_number:INTEGER,disc_number:INTEGER",
        description="Track metadata with album references (~256M tracks)",
    ),
    # === Track-Artist Relationships ===
    TableExtraction(
        sqlite_db="spotify_clean.sqlite3",
        query="""
            SELECT
                t.id as track_id,
                a.id as artist_id
            FROM track_artists ta
            JOIN tracks t ON ta.track_rowid = t.rowid
            JOIN artists a ON ta.artist_rowid = a.rowid
        """,
        bigquery_table="spotify_track_artists",
        schema="track_id:STRING,artist_id:STRING",
        description="Track-artist junction table (~300M rows, multi-artist tracks)",
    ),
    # === Audio Features ===
    TableExtraction(
        sqlite_db="spotify_clean_audio_features.sqlite3",
        query="""
            SELECT
                track_id,
                danceability,
                energy,
                loudness,
                speechiness,
                acousticness,
                instrumentalness,
                liveness,
                valence,
                tempo,
                duration_ms,
                time_signature,
                key,
                mode
            FROM track_audio_features
            WHERE null_response = 0 OR null_response IS NULL
        """,
        bigquery_table="spotify_audio_features",
        schema="track_id:STRING,danceability:FLOAT,energy:FLOAT,loudness:FLOAT,speechiness:FLOAT,acousticness:FLOAT,instrumentalness:FLOAT,liveness:FLOAT,valence:FLOAT,tempo:FLOAT,duration_ms:INTEGER,time_signature:INTEGER,key:INTEGER,mode:INTEGER",
        description="Audio features for tracks (~200M rows)",
    ),
]


def log(msg: str) -> None:
    """Print timestamped log message."""
    print(f"[{datetime.now().isoformat()}] {msg}")


def decompress_sqlite_files() -> None:
    """Decompress .zst SQLite files to WORK_DIR."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    zst_files = [
        "spotify_clean.sqlite3.zst",
        "spotify_clean_audio_features.sqlite3.zst",
    ]

    for zst_file in zst_files:
        src_path = SOURCE_DIR / zst_file
        dst_path = WORK_DIR / zst_file.replace(".zst", "")

        if dst_path.exists():
            log(f"  {dst_path.name} already exists, skipping decompression")
            continue

        if not src_path.exists():
            log(f"  WARNING: {src_path} not found")
            continue

        log(f"  Decompressing {zst_file}...")
        subprocess.run(
            ["zstd", "-d", "-o", str(dst_path), str(src_path)],
            check=True,
        )
        log(f"  Decompressed to {dst_path} ({dst_path.stat().st_size / 1024 / 1024 / 1024:.1f} GB)")


def archive_raw_files_to_gcs() -> None:
    """Archive raw .zst files to GCS Archive storage for permanent preservation."""
    # Use same archive bucket as audio analysis ETL for consistency
    gcs_archive_path = "gs://nomadkaraoke-raw-archives/spotify-metadata-2025-07/"

    zst_files = list(SOURCE_DIR.glob("*.zst"))
    if not zst_files:
        log("  No .zst files to archive")
        return

    total_size = sum(f.stat().st_size for f in zst_files) / 1024 / 1024 / 1024
    log(f"Archiving {len(zst_files)} raw .zst files ({total_size:.1f} GB) to {gcs_archive_path}")
    log("  Using Archive storage class (~$0.50/month for 186GB)")

    for zst_file in zst_files:
        size_gb = zst_file.stat().st_size / 1024 / 1024 / 1024
        log(f"  Uploading {zst_file.name} ({size_gb:.1f} GB)...")
        subprocess.run(
            [
                "gsutil",
                "-o",
                "GSUtil:parallel_composite_upload_threshold=150M",
                "cp",
                "-s",
                "ARCHIVE",  # Use Archive storage class
                str(zst_file),
                gcs_archive_path,
            ],
            check=True,
        )
    log("  Raw files archived to GCS")


def explore_sqlite_schema(db_path: Path) -> dict[str, list[tuple[str, str]]]:
    """Explore and return schema of a SQLite database."""
    if not db_path.exists():
        log(f"WARNING: {db_path} not found")
        return {}

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    schema: dict[str, list[tuple[str, str]]] = {}

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]

    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [(col[1], col[2]) for col in cursor.fetchall()]

        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]

        schema[table] = columns
        log(f"  {table}: {count:,} rows, columns: {[c[0] for c in columns]}")

    conn.close()
    return schema


def explore_all_schemas() -> None:
    """Explore and print schemas for all SQLite databases."""
    databases = [
        "spotify_clean.sqlite3",
        "spotify_clean_audio_features.sqlite3",
    ]

    for db_name in databases:
        db_path = WORK_DIR / db_name
        log(f"\n=== Schema for {db_name} ===")
        if db_path.exists():
            explore_sqlite_schema(db_path)
        else:
            log(f"  NOT FOUND: {db_path}")
            log("  Please download and decompress the database first.")


def extract_table(extraction: TableExtraction) -> int:
    """Extract a single table to NDJSON files."""
    db_path = WORK_DIR / extraction.sqlite_db
    if not db_path.exists():
        log(f"ERROR: {db_path} not found. Skipping {extraction.bigquery_table}.")
        return 0

    log(f"Extracting {extraction.bigquery_table} from {extraction.sqlite_db}...")
    log(f"  Description: {extraction.description}")

    # Create output directory for this table
    table_output_dir = OUTPUT_DIR / extraction.bigquery_table
    table_output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Execute query
    log("  Running query...")
    cursor.execute(extraction.query)

    # Get column names from cursor description
    column_names = [desc[0] for desc in cursor.description]

    file_num = 0
    row_count = 0
    total_count = 0
    current_file = None

    try:
        for row in cursor:
            if row_count == 0:
                file_path = table_output_dir / f"{extraction.bigquery_table}_{file_num:04d}.ndjson.gz"
                current_file = gzip.open(file_path, "wt", encoding="utf-8")

            # Convert row to dict with proper types
            record: dict[str, Any] = {}
            for i, col_name in enumerate(column_names):
                value = row[i]
                # Handle bytes
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                # Handle booleans (SQLite stores as 0/1)
                if col_name == "explicit" and value is not None:
                    value = bool(value)
                record[col_name] = value

            current_file.write(json.dumps(record) + "\n")

            row_count += 1
            total_count += 1

            if row_count >= BATCH_SIZE:
                current_file.close()
                current_file = None
                file_num += 1
                row_count = 0
                log(f"  Exported {total_count:,} rows...")
    finally:
        if current_file is not None:
            current_file.close()
        conn.close()
    log(f"  Complete: {total_count:,} rows in {file_num + 1} files")
    return file_num + 1


def upload_to_gcs(extraction: TableExtraction) -> None:
    """Upload NDJSON files for a table to GCS."""
    table_output_dir = OUTPUT_DIR / extraction.bigquery_table
    gcs_path = f"{GCS_BUCKET}/{extraction.bigquery_table}/"

    log(f"Uploading {extraction.bigquery_table} to {gcs_path}...")
    subprocess.run(
        ["gsutil", "-m", "cp", str(table_output_dir / "*.ndjson.gz"), gcs_path],
        check=True,
    )
    log("  Upload complete")


def load_to_bigquery(extraction: TableExtraction) -> None:
    """Load NDJSON files from GCS to BigQuery."""
    gcs_path = f"{GCS_BUCKET}/{extraction.bigquery_table}/*.ndjson.gz"
    table_ref = f"{PROJECT}:{DATASET}.{extraction.bigquery_table}"

    log(f"Loading {extraction.bigquery_table} to BigQuery...")
    subprocess.run(
        [
            "bq",
            "load",
            "--replace",
            "--source_format=NEWLINE_DELIMITED_JSON",
            table_ref,
            gcs_path,
            extraction.schema,
        ],
        check=True,
    )
    log("  BigQuery load complete")


def run_etl(table_filter: str | None = None, skip_decompress: bool = False, skip_gcs_sqlite: bool = False) -> None:
    """Run the complete ETL pipeline."""
    log("=" * 60)
    log("Complete Spotify Metadata ETL")
    log("=" * 60)

    # Step 1: Decompress SQLite files from .zst
    if not skip_decompress:
        log("\n--- Step 1: Decompress SQLite files ---")
        decompress_sqlite_files()
    else:
        log("\n--- Step 1: Skipping decompression (--skip-decompress) ---")

    # Step 2: Archive raw files to GCS for permanent preservation
    if not skip_gcs_sqlite:
        log("\n--- Step 2: Archive raw files to GCS ---")
        archive_raw_files_to_gcs()
    else:
        log("\n--- Step 2: Skipping GCS archive (--skip-gcs-sqlite) ---")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Filter extractions if specified
    extractions = EXTRACTIONS
    if table_filter:
        extractions = [e for e in EXTRACTIONS if e.bigquery_table == table_filter]
        if not extractions:
            log(f"ERROR: Unknown table '{table_filter}'")
            log(f"Available tables: {[e.bigquery_table for e in EXTRACTIONS]}")
            sys.exit(1)

    # Check that all required SQLite files exist
    required_dbs = set(e.sqlite_db for e in extractions)
    missing_dbs = []
    for db in required_dbs:
        db_path = WORK_DIR / db
        if not db_path.exists():
            missing_dbs.append(db)

    if missing_dbs:
        log("\nERROR: Missing SQLite databases:")
        for db in missing_dbs:
            log(f"  - {WORK_DIR / db}")
        log("\nPlease download and decompress the Spotify dump first.")
        log("See: https://annas-archive.li/blog/backing-up-spotify.html")
        sys.exit(1)

    # Process each table
    for extraction in extractions:
        log(f"\n{'=' * 60}")
        log(f"Processing: {extraction.bigquery_table}")
        log(f"{'=' * 60}")

        # Extract
        num_files = extract_table(extraction)
        if num_files == 0:
            continue

        # Upload
        upload_to_gcs(extraction)

        # Load
        load_to_bigquery(extraction)

    log(f"\n{'=' * 60}")
    log("ETL Complete!")
    log(f"{'=' * 60}")

    # Print summary
    log("\nTables loaded to BigQuery:")
    for e in extractions:
        log(f"  - {DATASET}.{e.bigquery_table}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Complete Spotify Metadata ETL")
    parser.add_argument("--explore", action="store_true", help="Only explore schemas, don't extract")
    parser.add_argument("--table", type=str, help="Extract only a specific table")
    parser.add_argument("--skip-decompress", action="store_true", help="Skip decompression step")
    parser.add_argument("--skip-gcs-sqlite", action="store_true", help="Skip uploading SQLite to GCS")
    args = parser.parse_args()

    if args.explore:
        explore_all_schemas()
    else:
        run_etl(args.table, args.skip_decompress, args.skip_gcs_sqlite)


if __name__ == "__main__":
    main()
