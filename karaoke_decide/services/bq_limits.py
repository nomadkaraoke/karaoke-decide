"""BigQuery ``maximum_bytes_billed`` guards.

BigQuery on-demand pricing bills per byte scanned, so a bad query (a dropped
filter, a regressed join, a runaway loop) can burn terabytes. Every query
config gets a cap: if BigQuery estimates the query will scan more than the
cap it fails *before running*, at no cost.

IMPORTANT: the cap is compared against BigQuery's *pre-pruning* estimate (the
dry-run ``total_bytes_processed``), NOT the post-clustering bytes actually
billed. E.g. a search against ``mb_recordings_enriched`` bills ~50 MiB but is
estimated at ~6.3 GiB, so its cap must sit above 6.3 GiB. When changing a
query, check its estimate with ``bq query --dry_run`` and keep it well under
the cap for its class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# google.cloud.bigquery is imported inside the functions that use it: importing it costs
# ~0.5 s, so this keeps it out of the main API's cold start (spec §6).
if TYPE_CHECKING:
    from google.cloud import bigquery


GIB = 1024**3

# Client-wide fallback for any query that doesn't set its own cap. Above every
# request-path query we run (largest historical: ~15 GiB), well below "TB burn".
MAX_BYTES_DEFAULT = 20 * GIB

# Recording search / lookup against mb_recordings_enriched (estimate ~6.3 GiB).
MAX_BYTES_RECORDING_SEARCH = 10 * GIB

# Recommendation queries joining karaokenerds_raw with
# spotify_popularity_by_artist_title (estimate ~1.7 GiB).
MAX_BYTES_RECOMMENDATION = 5 * GIB

# Offline/batch CLI jobs that deliberately full-scan spotify_tracks +
# spotify_audio_features (estimate ~32 GiB, run once per candidates batch).
MAX_BYTES_BATCH = 50 * GIB

# Offline catalog.db build (karaoke_decide/catalog/bq_source.py); largest query is the
# song-facets join (dry run 2026-10-21: all six queries total 43.9 GiB, largest 32.1 GiB; cap = 1.5x total).
MAX_BYTES_CATALOG = 70 * GIB


def default_query_job_config() -> bigquery.QueryJobConfig:
    """Client-wide default job config (merged into every query's config)."""
    from google.cloud import bigquery

    return bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_DEFAULT)


def make_client(project: str) -> bigquery.Client:
    """Create a BigQuery client with the default bytes-billed cap applied.

    Raises ``RuntimeError("bigquery disabled")`` under ``BIGQUERY_DISABLED`` (local stack, tests)."""
    from karaoke_decide.core.config import get_settings

    if get_settings().bigquery_disabled:
        raise RuntimeError("bigquery disabled")
    from google.cloud import bigquery

    return bigquery.Client(project=project, default_query_job_config=default_query_job_config())
