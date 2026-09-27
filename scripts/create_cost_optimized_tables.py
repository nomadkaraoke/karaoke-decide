#!/usr/bin/env python3
"""Create cost-optimized derived BigQuery tables for hot query paths.

BigQuery bills by bytes scanned, and the source tables here are large and
unclustered (spotify_tracks is 256M rows / ~34 GB). Two request-path queries
used to join them in full on every call:

1. ``mb_recordings_enriched`` — used by ``BigQueryCatalogService.search_recordings``
   and ``get_recording_by_mbid``. Pre-joins
   ``mb_recordings`` x ``mb_recording_isrc`` x ``spotify_tracks`` (LEFT JOINs, same
   fan-out as the original query) and precomputes the normalized artist credit.
   Clustered on ``name_normalized, artist_normalized`` so prefix ``LIKE``
   filters prune blocks. Was ~15 GiB billed per search call.

2. ``spotify_popularity_by_artist_title`` — used by
   ``RecommendationService._get_songs_by_artists`` / ``_get_popular_songs``.
   ``MAX(spotify_tracks.popularity)`` grouped by ``(LOWER(artist_name), LOWER(title))``,
   keeping only popularity > 0 (0/NULL contribute nothing beyond the
   ``COALESCE(MAX(...), 0)`` in the consumer query, so results are identical).
   Clustered on ``artist_lower`` so ``IN (...)`` artist filters prune.
   Was ~11 GiB billed per call.

The source data (MusicBrainz + Spotify dumps) is static between manual ETL
runs. Re-run this script after re-running ``scripts/musicbrainz_etl.py`` or
reloading ``spotify_tracks``.

Usage:
    python3 scripts/create_cost_optimized_tables.py            # build both
    python3 scripts/create_cost_optimized_tables.py --only mb_recordings_enriched
    python3 scripts/create_cost_optimized_tables.py --dry-run  # print SQL + estimate
"""

import argparse
import logging
from datetime import datetime

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ID = "nomadkaraoke"
DATASET_ID = "karaoke_decide"
DS = f"{PROJECT_ID}.{DATASET_ID}"

# Must stay byte-identical to the runtime artist normalization previously used in
# search_recordings (NFD decompose, strip combining marks, lowercase,
# non-alphanumerics -> space, collapse spaces, trim).
ARTIST_NORMALIZE_SQL = (
    "TRIM(REGEXP_REPLACE(REGEXP_REPLACE("
    "LOWER(REGEXP_REPLACE(NORMALIZE(r.artist_credit, NFD), r'\\pM', '')), "
    "r'[^a-z0-9 ]', ' '), r' +', ' '))"
)

TABLES: dict[str, str] = {
    "mb_recordings_enriched": f"""
    CREATE OR REPLACE TABLE `{DS}.mb_recordings_enriched`
    CLUSTER BY name_normalized, artist_normalized
    AS
    SELECT
        r.recording_mbid,
        r.title,
        r.artist_credit,
        r.length_ms,
        r.disambiguation,
        r.name_normalized,
        {ARTIST_NORMALIZE_SQL} AS artist_normalized,
        st.spotify_id AS spotify_track_id,
        st.popularity AS spotify_popularity
    FROM `{DS}.mb_recordings` r
    LEFT JOIN `{DS}.mb_recording_isrc` ri
        ON r.recording_mbid = ri.recording_mbid
    LEFT JOIN `{DS}.spotify_tracks` st
        ON ri.isrc = st.isrc
    """,
    "spotify_popularity_by_artist_title": f"""
    CREATE OR REPLACE TABLE `{DS}.spotify_popularity_by_artist_title`
    CLUSTER BY artist_lower, title_lower
    AS
    SELECT
        LOWER(artist_name) AS artist_lower,
        LOWER(title) AS title_lower,
        MAX(popularity) AS popularity
    FROM `{DS}.spotify_tracks`
    WHERE popularity > 0
      AND artist_name IS NOT NULL
      AND title IS NOT NULL
    GROUP BY 1, 2
    """,
}


def build_table(client: bigquery.Client, name: str, dry_run: bool) -> None:
    sql = TABLES[name]
    if dry_run:
        job = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
        print(sql)
        logger.info(f"{name}: would process {job.total_bytes_processed / 1024**3:.2f} GiB")
        return

    logger.info(f"Building {DS}.{name} ...")
    start = datetime.now()
    client.query(sql).result()
    table = client.get_table(f"{DS}.{name}")
    logger.info(
        f"{name}: {table.num_rows:,} rows, {table.num_bytes / 1024**3:.2f} GiB, "
        f"built in {(datetime.now() - start).total_seconds():.0f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(TABLES), help="Build a single table")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL and estimated bytes only")
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT_ID)
    for name in [args.only] if args.only else list(TABLES):
        build_table(client, name, args.dry_run)


if __name__ == "__main__":
    main()
