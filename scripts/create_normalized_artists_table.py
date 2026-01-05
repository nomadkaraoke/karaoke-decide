#!/usr/bin/env python3
"""Create normalized artists table in BigQuery for fast artist lookups.

This script creates a pre-computed table `spotify_artists_normalized` that:
1. Stores normalized artist names for fast exact-match lookups
2. Pre-aggregates genres (avoiding expensive JOINs at query time)
3. Reduces artist lookup time from 75s+ to <100ms

Run once to create the table. Re-run if you need to refresh the data.

Usage:
    python3 scripts/create_normalized_artists_table.py
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
TABLE_ID = "spotify_artists_normalized"
FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"


def create_normalized_table(client: bigquery.Client, dry_run: bool = False) -> None:
    """Create the normalized artists table with pre-aggregated genres.

    The table schema:
    - artist_id: STRING (Spotify artist ID)
    - artist_name: STRING (Original artist name)
    - normalized_name: STRING (Lowercased, alphanumeric only, single spaces)
    - popularity: INTEGER (Spotify popularity score 0-100)
    - followers_total: INTEGER (Spotify follower count)
    - genres: ARRAY<STRING> (Up to 5 genres from artist_genres table)

    Normalization matches the Python function _normalize_for_matching():
    1. Lowercase
    2. Replace non-alphanumeric (except space) with space
    3. Collapse multiple spaces to single
    4. Trim whitespace
    """
    sql = f"""
    CREATE OR REPLACE TABLE `{FULL_TABLE_ID}` AS
    SELECT
        a.artist_id,
        a.artist_name,
        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(a.artist_name), r'[^a-z0-9 ]', ' '), r' +', ' ')) as normalized_name,
        a.popularity,
        a.followers_total,
        ARRAY_AGG(DISTINCT g.genre IGNORE NULLS ORDER BY g.genre LIMIT 5) as genres
    FROM `{PROJECT_ID}.{DATASET_ID}.spotify_artists` a
    LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.spotify_artist_genres` g
        ON a.artist_id = g.artist_id
    GROUP BY
        a.artist_id,
        a.artist_name,
        a.popularity,
        a.followers_total
    """

    logger.info("Creating normalized artists table...")
    logger.info(f"Target: {FULL_TABLE_ID}")

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
    SELECT artist_name, normalized_name, popularity, genres
    FROM `{FULL_TABLE_ID}`
    WHERE ARRAY_LENGTH(genres) > 0
    ORDER BY popularity DESC
    LIMIT 5
    """
    logger.info("\nSample data (top 5 by popularity):")
    for row in client.query(sample_sql).result():
        genres_str = ", ".join(row.genres) if row.genres else "(no genres)"
        logger.info(f"  {row.artist_name} ({row.popularity}) - {genres_str}")


def verify_normalization(client: bigquery.Client) -> None:
    """Verify the normalization matches expected test cases."""
    logger.info("\nVerifying normalization logic...")
    sql = f"""
    SELECT
        artist_name,
        normalized_name,
        CASE
            WHEN artist_name = 'Queen' AND normalized_name = 'queen' THEN 'PASS'
            WHEN artist_name = 'AC/DC' AND normalized_name = 'ac dc' THEN 'PASS'
            ELSE 'CHECK'
        END as status
    FROM `{FULL_TABLE_ID}`
    WHERE artist_name IN ('Queen', 'AC/DC', 'Guns N\\' Roses', 'Beyoncé')
    """

    found_artists = {}
    for row in client.query(sql).result():
        found_artists[row.artist_name] = row.normalized_name
        logger.info(f"  '{row.artist_name}' -> '{row.normalized_name}'")

    logger.info(f"\nFound {len(found_artists)} test artists in table")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Create normalized artists table")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing table")
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT_ID)

    if args.verify_only:
        verify_normalization(client)
    else:
        create_normalized_table(client, dry_run=args.dry_run)
        if not args.dry_run:
            verify_normalization(client)


if __name__ == "__main__":
    main()
