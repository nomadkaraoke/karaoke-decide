#!/usr/bin/env python3
"""Create normalized tracks table in BigQuery for fast song lookups.

This script creates a pre-computed table `spotify_tracks_normalized` that:
1. Stores normalized track titles for fast prefix matching
2. Combines track and artist data in one table
3. Filters to only popular tracks (popularity >= 30) to reduce table size
4. Enables fast song autocomplete searches

The full spotify_tracks table has 256M rows - this creates a smaller, faster
table for autocomplete by filtering to popular tracks only.

Run once to create the table. Re-run if you need to refresh the data.

Usage:
    python3 scripts/create_normalized_tracks_table.py
"""

import argparse
import logging
from datetime import datetime

from google.cloud import bigquery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
TABLE_ID = "spotify_tracks_normalized"
FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# Minimum popularity to include (0-100 scale)
# 30+ covers ~20M tracks, which is still comprehensive but faster
MIN_POPULARITY = 30


def create_normalized_table(client: bigquery.Client, dry_run: bool = False) -> None:
    """Create the normalized tracks table for fast autocomplete.

    The table schema:
    - track_id: STRING (Spotify track ID)
    - track_name: STRING (Original track name)
    - normalized_title: STRING (Lowercased, alphanumeric only, single spaces)
    - artist_name: STRING (Primary artist name)
    - normalized_artist: STRING (Normalized artist name for search)
    - artist_id: STRING (Spotify artist ID)
    - popularity: INTEGER (Spotify popularity score 0-100)
    - duration_ms: INTEGER (Track duration)
    - explicit: BOOLEAN (Explicit content flag)

    Normalization matches the Python function _normalize_for_matching():
    1. Lowercase
    2. Replace non-alphanumeric (except space) with space
    3. Collapse multiple spaces to single
    4. Trim whitespace
    """
    sql = f"""
    CREATE OR REPLACE TABLE `{FULL_TABLE_ID}` AS
    SELECT
        t.spotify_id as track_id,
        t.title as track_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(t.title), r'[^a-z0-9 ]', ' '), r' +', ' ')) as normalized_title,
        t.artist_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(t.artist_name), r'[^a-z0-9 ]', ' '), r' +', ' ')) as normalized_artist,
        t.artist_spotify_id as artist_id,
        t.popularity,
        t.duration_ms,
        t.explicit
    FROM `{PROJECT_ID}.{DATASET_ID}.spotify_tracks` t
    WHERE t.popularity >= {MIN_POPULARITY}
    """

    logger.info("Creating normalized tracks table...")
    logger.info(f"Target: {FULL_TABLE_ID}")
    logger.info(f"Min popularity filter: {MIN_POPULARITY}")

    if dry_run:
        logger.info("DRY RUN - SQL that would be executed:")
        print(sql)
        return

    # Configure job
    job_config = bigquery.QueryJobConfig(
        use_legacy_sql=False,
    )

    # Execute
    start_time = datetime.now()
    query_job = client.query(sql, job_config=job_config)
    query_job.result()  # Wait for completion
    elapsed = (datetime.now() - start_time).total_seconds()

    logger.info(f"Table created in {elapsed:.1f}s")

    # Verify table stats
    table = client.get_table(FULL_TABLE_ID)
    logger.info(f"Table rows: {table.num_rows:,}")
    logger.info(f"Table size: {table.num_bytes / 1024 / 1024:.1f} MB")

    # Sample data
    sample_sql = f"""
    SELECT track_name, artist_name, normalized_title, popularity
    FROM `{FULL_TABLE_ID}`
    ORDER BY popularity DESC
    LIMIT 10
    """
    logger.info("\nSample data (top 10 by popularity):")
    for row in client.query(sample_sql).result():
        logger.info(f"  '{row.track_name}' by {row.artist_name} ({row.popularity})")


def test_search(client: bigquery.Client, query: str) -> None:
    """Test a search query against the normalized table."""
    from karaoke_decide.services.bigquery_catalog import _normalize_for_matching

    normalized = _normalize_for_matching(query)
    logger.info(f"\nTesting search for: '{query}' (normalized: '{normalized}')")

    sql = f"""
    SELECT
        track_name,
        artist_name,
        popularity
    FROM `{FULL_TABLE_ID}`
    WHERE normalized_title LIKE @query_prefix
       OR normalized_artist LIKE @query_prefix
    ORDER BY popularity DESC
    LIMIT 10
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
        ]
    )

    start = datetime.now()
    results = list(client.query(sql, job_config=job_config).result())
    elapsed_ms = (datetime.now() - start).total_seconds() * 1000

    logger.info(f"Query took: {elapsed_ms:.0f}ms")
    logger.info(f"Results ({len(results)}):")
    for row in results:
        logger.info(f"  '{row.track_name}' by {row.artist_name} ({row.popularity})")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Create normalized tracks table")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    parser.add_argument("--test", type=str, help="Test search query against existing table")
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT_ID)

    if args.test:
        test_search(client, args.test)
    else:
        create_normalized_table(client, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
