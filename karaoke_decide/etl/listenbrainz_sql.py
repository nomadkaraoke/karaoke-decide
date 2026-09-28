"""SQL models for the ListenBrainz refresh pipeline.

Source: the ListenBrainz *statistics* dump (CC0), published with every full
export (1st and 15th of the month). Each ``<entity>_<range>.jsonl`` file holds
one line per ListenBrainz user::

    {"user_id": 1, "count": 8629, "from_ts": ..., "to_ts": ..., "last_updated": ...,
     "data": [{"listen_count": 4298, "artist_mbid": "...", "artist_name": "..."}, ...]}

``data`` is that user's **top 1,000** entities for the range (``count`` is the
user's total distinct entities), so summing across users slightly undercounts
the long tail. That's fine for ranking; exact counts would need the 229 GB raw
listens dump. Items without an MBID (ListenBrainz couldn't map the listen,
~15-20% of items) are dropped.

Each file is loaded as-is into ``listenbrainz_staging.raw_<entity>_<range>``
with a minimal nested schema (``ignore_unknown_values`` drops the rest), then
the models below aggregate by MBID in SQL. MBIDs MusicBrainz has since merged
are resolved through the ``mb_*_redirects`` tables (refreshed by mb-refresh).
"""

from google.cloud import bigquery

from karaoke_decide.etl.musicbrainz_sql import PROD_DATASET, PROJECT_ID, P

STAGING_DATASET = "listenbrainz_staging"
S = f"{PROJECT_ID}.{STAGING_DATASET}"

__all__ = ["P", "PROD_DATASET", "PROJECT_ID", "S", "STAGING_DATASET"]

# ListenBrainz stats ranges. The rolling-sounding ones are calendar periods:
# week/month/quarter/half_yearly/year = the last *completed* one;
# this_week/this_month/this_year = the current one so far; all_time = everything.
STATS_RANGES: list[str] = [
    "all_time",
    "year",
    "half_yearly",
    "quarter",
    "month",
    "week",
    "this_year",
    "this_month",
    "this_week",
]

# entity -> (MBID field inside each data item, prod MusicBrainz redirect table)
ENTITIES: dict[str, tuple[str, str]] = {
    "artists": ("artist_mbid", "mb_artist_redirects"),
    "recordings": ("recording_mbid", "mb_recording_redirects"),
}

RAW_FILES: list[str] = [f"{entity}_{rng}" for entity in ENTITIES for rng in STATS_RANGES]


def raw_schema(entity: str) -> list[bigquery.SchemaField]:
    """Only the fields the models read; everything else is ignored at load time."""
    mbid_field, _ = ENTITIES[entity]
    return [
        bigquery.SchemaField("user_id", "INT64"),
        bigquery.SchemaField("count", "INT64"),
        bigquery.SchemaField("from_ts", "INT64"),
        bigquery.SchemaField("to_ts", "INT64"),
        bigquery.SchemaField(
            "data",
            "RECORD",
            mode="REPEATED",
            fields=[
                bigquery.SchemaField("listen_count", "INT64"),
                bigquery.SchemaField(mbid_field, "STRING"),
            ],
        ),
    ]


def _items(entity: str, ranges: list[str]) -> str:
    """UNION ALL of (stats_range, user_id, mbid, listen_count) with redirects resolved."""
    mbid_field, redirects = ENTITIES[entity]
    selects = "\n        UNION ALL\n".join(
        f"""        SELECT '{rng}' AS stats_range, r.user_id, d.{mbid_field} AS mbid, d.listen_count
        FROM `{S}.raw_{entity}_{rng}` r, UNNEST(r.data) d
        WHERE d.{mbid_field} IS NOT NULL AND d.listen_count > 0"""
        for rng in ranges
    )
    return f"""
    items AS (
{selects}
    ),
    resolved AS (
        SELECT i.stats_range, i.user_id, COALESCE(rd.{mbid_field}, i.mbid) AS mbid, i.listen_count
        FROM items i
        LEFT JOIN `{P}.{redirects}` rd ON rd.old_mbid = i.mbid
    )"""


def _popularity_model(entity: str, table: str) -> str:
    mbid_field, _ = ENTITIES[entity]
    return f"""
    CREATE OR REPLACE TABLE `{S}.{table}`
    CLUSTER BY stats_range, {mbid_field}
    AS
    WITH {_items(entity, STATS_RANGES)}
    SELECT
        stats_range,
        mbid AS {mbid_field},
        SUM(listen_count) AS total_listens,
        COUNT(DISTINCT user_id) AS listeners
    FROM resolved
    GROUP BY stats_range, mbid
    """


def _ranges_model() -> str:
    selects = "\n    UNION ALL\n".join(
        f"""    SELECT '{entity}' AS entity, '{rng}' AS stats_range,
        COUNT(*) AS users,
        TIMESTAMP_SECONDS(MIN(from_ts)) AS from_ts,
        TIMESTAMP_SECONDS(MAX(to_ts)) AS to_ts
    FROM `{S}.raw_{entity}_{rng}`"""
        for entity in ENTITIES
        for rng in STATS_RANGES
    )
    return f"""
    CREATE OR REPLACE TABLE `{S}.lb_stats_ranges` AS
{selects}
    """


MODELS: dict[str, str] = {
    # One row per (entity, range): how many users contributed and the period
    # covered. Use `users` to turn listener counts into a share of all users.
    "lb_stats_ranges": _ranges_model(),
    "lb_artist_popularity": _popularity_model("artists", "lb_artist_popularity"),
    "lb_recording_popularity": _popularity_model("recordings", "lb_recording_popularity"),
    # Per-user all-time top artists: input for collaborative filtering
    # ("listeners of X also like Y"), a fresher MBID-keyed complement to MLHD+.
    # user_id is ListenBrainz's own numeric id; no user names are stored.
    "lb_user_artist_listens": f"""
    CREATE OR REPLACE TABLE `{S}.lb_user_artist_listens`
    CLUSTER BY artist_mbid, user_id
    AS
    WITH {_items("artists", ["all_time"])}
    SELECT user_id, mbid AS artist_mbid, SUM(listen_count) AS listen_count
    FROM resolved
    GROUP BY user_id, mbid
    """,
}

MODEL_ORDER: list[str] = list(MODELS)

# Row-count bounds relative to current prod (min_ratio, max_ratio). Consecutive
# dumps are two weeks apart and ListenBrainz keeps growing; the short ranges
# (week, this_week) swing more, but they're a small share of each table.
ROW_COUNT_BOUNDS: dict[str, tuple[float, float] | None] = {
    "lb_stats_ranges": (1.0, 1.0),
    "lb_artist_popularity": (0.80, 1.40),
    "lb_recording_popularity": (0.80, 1.40),
    "lb_user_artist_listens": (0.90, 1.30),
}

# Well-known MBIDs used as canaries.
RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
CREEP = "70595637-9310-45f2-a266-58f8de4874a7"

_EXPECTED_RANGES = len(ENTITIES) * len(STATS_RANGES)

# Sanity checks against staging. Each returns one BOOL ``ok`` and a ``detail``.
CANARY_CHECKS: dict[str, str] = {
    "all_ranges_have_users": f"""
        SELECT COUNT(*) = {_EXPECTED_RANGES} AND MIN(users) > 0 AS ok,
               FORMAT('ranges=%d min_users=%d', COUNT(*), MIN(users)) AS detail
        FROM `{S}.lb_stats_ranges`
    """,
    "all_time_users": f"""
        SELECT MIN(users) > 50000 AS ok, FORMAT('min_users=%d', MIN(users)) AS detail
        FROM `{S}.lb_stats_ranges` WHERE stats_range = 'all_time'
    """,
    "radiohead_popular": f"""
        SELECT COALESCE(MAX(listeners), 0) > 2000 AS ok,
               FORMAT('listeners=%d', COALESCE(MAX(listeners), 0)) AS detail
        FROM `{S}.lb_artist_popularity`
        WHERE stats_range = 'all_time' AND artist_mbid = '{RADIOHEAD}'
    """,
    "creep_popular": f"""
        SELECT COALESCE(MAX(listeners), 0) > 200 AS ok,
               FORMAT('listeners=%d', COALESCE(MAX(listeners), 0)) AS detail
        FROM `{S}.lb_recording_popularity`
        WHERE stats_range = 'all_time' AND recording_mbid = '{CREEP}'
    """,
    "every_range_populated": f"""
        SELECT COUNT(DISTINCT a.stats_range) = {len(STATS_RANGES)}
               AND COUNT(DISTINCT r.stats_range) = {len(STATS_RANGES)} AS ok,
               FORMAT('artist_ranges=%d recording_ranges=%d',
                      COUNT(DISTINCT a.stats_range), COUNT(DISTINCT r.stats_range)) AS detail
        FROM (SELECT DISTINCT stats_range FROM `{S}.lb_artist_popularity`) a
        FULL JOIN (SELECT DISTINCT stats_range FROM `{S}.lb_recording_popularity`) r USING (stats_range)
    """,
    "artist_keys_unique": f"""
        SELECT COUNT(*) = COUNT(DISTINCT CONCAT(stats_range, artist_mbid)) AS ok,
               FORMAT('rows=%d', COUNT(*)) AS detail
        FROM `{S}.lb_artist_popularity`
    """,
    "recording_keys_unique": f"""
        SELECT COUNT(*) = COUNT(DISTINCT CONCAT(stats_range, recording_mbid)) AS ok,
               FORMAT('rows=%d', COUNT(*)) AS detail
        FROM `{S}.lb_recording_popularity`
    """,
    # ListenBrainz maps listens to MusicBrainz, so (after redirects) nearly all
    # of the listen volume should land on recordings we have. A low share means
    # a join/redirect bug or a stale mb_recordings.
    "recordings_join_musicbrainz": f"""
        SELECT SAFE_DIVIDE(SUM(IF(m.recording_mbid IS NOT NULL, p.total_listens, 0)), SUM(p.total_listens)) > 0.95 AS ok,
               FORMAT('%.4f', SAFE_DIVIDE(SUM(IF(m.recording_mbid IS NOT NULL, p.total_listens, 0)),
                                          SUM(p.total_listens))) AS detail
        FROM `{S}.lb_recording_popularity` p
        LEFT JOIN `{P}.mb_recordings` m USING (recording_mbid)
        WHERE p.stats_range = 'all_time'
    """,
    "listeners_within_users": f"""
        SELECT COUNTIF(p.listeners > r.users) = 0 AS ok, CAST(COUNTIF(p.listeners > r.users) AS STRING) AS detail
        FROM `{S}.lb_artist_popularity` p
        JOIN `{S}.lb_stats_ranges` r ON r.entity = 'artists' AND r.stats_range = p.stats_range
    """,
}
