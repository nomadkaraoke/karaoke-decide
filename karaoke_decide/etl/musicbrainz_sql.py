"""SQL models for the MusicBrainz refresh pipeline.

The raw MusicBrainz dump tables are PostgreSQL ``COPY`` text files (TSV, ``\\N``
for NULL, backslash escapes). They are loaded verbatim into
``musicbrainz_staging.raw_<table>`` with every column as STRING and named by
position (``c0``, ``c1``, ...), mirroring how the original Python ETL
(``scripts/musicbrainz_etl.py``) indexed columns. Loading with an exact column
count means BigQuery rejects the file if MusicBrainz ever changes a table's
shape, so a schema change fails the run instead of silently shifting columns.

The models below rebuild every MusicBrainz-derived table that the app reads,
with the same columns and semantics as the tables built by the original
one-off ETL scripts. They run in ``MODEL_ORDER`` and write to the staging
dataset; ``musicbrainz_refresh`` validates them and then copies them to prod.
"""

PROJECT_ID = "nomadkaraoke"
PROD_DATASET = "karaoke_decide"
STAGING_DATASET = "musicbrainz_staging"

P = f"{PROJECT_ID}.{PROD_DATASET}"
S = f"{PROJECT_ID}.{STAGING_DATASET}"

# Dump archive -> {table name inside mbdump/: column count}.
# Column counts follow admin/sql/CreateTables.sql in musicbrainz-server.
RAW_TABLES: dict[str, dict[str, int]] = {
    "mbdump.tar.bz2": {
        "artist": 19,
        "artist_type": 6,
        "area": 14,
        "gender": 6,
        "artist_credit": 7,
        "recording": 9,
        "isrc": 5,
        "url": 5,
        "l_artist_url": 9,
        "artist_gid_redirect": 3,
        "recording_gid_redirect": 3,
    },
    "mbdump-derived.tar.bz2": {
        "tag": 3,
        "artist_tag": 4,
    },
}

# Decode PostgreSQL COPY text escapes (\\ \t \n \r) and treat '' as NULL,
# matching parse_string() in the original ETL. \N is already NULL via the
# load job's null_marker.
PG_DECODE_FN = r"""
CREATE TEMP FUNCTION pg(s STRING) AS (
  NULLIF(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
      s, '\\\\', '\uE000'), '\\t', '\t'), '\\n', '\n'), '\\r', '\r'), '\uE000', '\\'),
    ''
  )
);
"""

# Must stay identical to the Python normalization used at query time
# (BigQueryCatalogService._normalize_for_matching / normalize_for_matching in
# the original scripts): lowercase, non [a-z0-9 ] -> space, collapse, trim.
NORM_FN = r"""
CREATE TEMP FUNCTION norm(s STRING) AS (
  TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(s), r'[^a-z0-9 ]', ' '), r' +', ' '))
);
"""

# Artist normalization used by mb_recordings_enriched.artist_normalized
# (NFD decompose + strip combining marks first). Must stay byte-identical to
# what BigQueryCatalogService.search_recordings compares against.
ARTIST_NORMALIZE_SQL = (
    "TRIM(REGEXP_REPLACE(REGEXP_REPLACE("
    "LOWER(REGEXP_REPLACE(NORMALIZE(r.artist_credit, NFD), r'\\pM', '')), "
    "r'[^a-z0-9 ]', ' '), r' +', ' '))"
)


def mb_recordings_enriched_sql(source_dataset: str, target_dataset: str) -> str:
    """mb_recordings x mb_recording_isrc x spotify_tracks, clustered for search.

    Shared with scripts/create_cost_optimized_tables.py so there is one
    definition of this table.
    """
    return f"""
    CREATE OR REPLACE TABLE `{target_dataset}.mb_recordings_enriched`
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
    FROM `{source_dataset}.mb_recordings` r
    LEFT JOIN `{source_dataset}.mb_recording_isrc` ri
        ON r.recording_mbid = ri.recording_mbid
    LEFT JOIN `{P}.spotify_tracks` st
        ON ri.isrc = st.isrc
    """


MODELS: dict[str, str] = {
    "mb_artists": f"""
    CREATE OR REPLACE TABLE `{S}.mb_artists` AS
    SELECT
        a.c1 AS artist_mbid,
        pg(a.c2) AS name,
        pg(a.c3) AS sort_name,
        pg(a.c13) AS disambiguation,
        pg(t.c1) AS artist_type,
        SAFE_CAST(a.c4 AS INT64) AS begin_year,
        SAFE_CAST(a.c7 AS INT64) AS end_year,
        pg(ar.c2) AS area_name,
        pg(g.c1) AS gender,
        norm(pg(a.c2)) AS name_normalized
    FROM `{S}.raw_artist` a
    LEFT JOIN `{S}.raw_artist_type` t ON t.c0 = a.c10
    LEFT JOIN `{S}.raw_area` ar ON ar.c0 = a.c11
    LEFT JOIN `{S}.raw_gender` g ON g.c0 = a.c12
    WHERE a.c1 IS NOT NULL AND pg(a.c2) IS NOT NULL
    """,
    "mb_artist_tags": f"""
    CREATE OR REPLACE TABLE `{S}.mb_artist_tags` AS
    SELECT
        a.c1 AS artist_mbid,
        pg(t.c1) AS tag_name,
        SAFE_CAST(atg.c2 AS INT64) AS vote_count
    FROM `{S}.raw_artist_tag` atg
    JOIN `{S}.raw_artist` a ON a.c0 = atg.c0
    JOIN `{S}.raw_tag` t ON t.c0 = atg.c1
    WHERE pg(t.c1) IS NOT NULL AND SAFE_CAST(atg.c2 AS INT64) IS NOT NULL
    """,
    "mb_recordings": f"""
    CREATE OR REPLACE TABLE `{S}.mb_recordings` AS
    SELECT
        r.c1 AS recording_mbid,
        pg(r.c2) AS title,
        SAFE_CAST(r.c4 AS INT64) AS length_ms,
        pg(ac.c1) AS artist_credit,
        SAFE_CAST(r.c3 AS INT64) AS artist_credit_id,
        pg(r.c5) AS disambiguation,
        r.c8 = 't' AS video,
        norm(pg(r.c2)) AS name_normalized
    FROM `{S}.raw_recording` r
    LEFT JOIN `{S}.raw_artist_credit` ac ON ac.c0 = r.c3
    WHERE r.c1 IS NOT NULL AND pg(r.c2) IS NOT NULL
    """,
    "mb_recording_isrc": f"""
    CREATE OR REPLACE TABLE `{S}.mb_recording_isrc` AS
    SELECT
        r.c1 AS recording_mbid,
        TRIM(i.c2) AS isrc
    FROM `{S}.raw_isrc` i
    JOIN `{S}.raw_recording` r ON r.c0 = i.c1
    WHERE i.c2 IS NOT NULL
    """,
    # Spotify artist links from MusicBrainz artist-URL relationships. The
    # original table came from the same relationships via the MB web API, but
    # only for the MLHD artist subset; the dump covers every artist. One row
    # per artist (prefer the Spotify ID we have catalog data for) so joins in
    # mb_artists_normalized don't fan out.
    "mbid_spotify_mapping": f"""
    CREATE OR REPLACE TABLE `{S}.mbid_spotify_mapping` AS
    WITH links AS (
        SELECT
            a.c1 AS artist_mbid,
            pg(a.c2) AS artist_name,
            REGEXP_EXTRACT(
                pg(u.c2),
                r'^https?://open\\.spotify\\.com/(?:intl-[a-z-]+/)?artist/([A-Za-z0-9]{{22}})'
            ) AS spotify_artist_id
        FROM `{S}.raw_l_artist_url` l
        JOIN `{S}.raw_url` u ON u.c0 = l.c3
        JOIN `{S}.raw_artist` a ON a.c0 = l.c2
    ),
    spotify_pop AS (
        SELECT artist_id, MAX(popularity) AS popularity
        FROM `{P}.spotify_artists_normalized`
        GROUP BY artist_id
    )
    SELECT artist_mbid, spotify_artist_id, artist_name
    FROM (
        SELECT
            l.*,
            ROW_NUMBER() OVER (
                PARTITION BY l.artist_mbid
                ORDER BY sp.popularity DESC NULLS LAST, l.spotify_artist_id
            ) AS rn
        FROM links l
        LEFT JOIN spotify_pop sp ON sp.artist_id = l.spotify_artist_id
        WHERE l.spotify_artist_id IS NOT NULL
    )
    WHERE rn = 1
    """,
    # Old MBID -> current MBID for entities MusicBrainz has merged, so MBIDs
    # we stored before a merge (Firestore quiz/user data) keep resolving.
    "mb_artist_redirects": f"""
    CREATE OR REPLACE TABLE `{S}.mb_artist_redirects`
    CLUSTER BY old_mbid
    AS
    SELECT r.c0 AS old_mbid, a.c1 AS artist_mbid
    FROM `{S}.raw_artist_gid_redirect` r
    JOIN `{S}.raw_artist` a ON a.c0 = r.c1
    """,
    "mb_recording_redirects": f"""
    CREATE OR REPLACE TABLE `{S}.mb_recording_redirects`
    CLUSTER BY old_mbid
    AS
    SELECT r.c0 AS old_mbid, rec.c1 AS recording_mbid
    FROM `{S}.raw_recording_gid_redirect` r
    JOIN `{S}.raw_recording` rec ON rec.c0 = r.c1
    """,
    "mb_artists_normalized": f"""
    CREATE OR REPLACE TABLE `{S}.mb_artists_normalized` AS
    SELECT
        a.artist_mbid,
        a.name AS artist_name,
        a.name_normalized,
        a.disambiguation,
        a.artist_type,
        a.begin_year,
        a.area_name,
        m.spotify_artist_id,
        COALESCE(s.popularity, 50) AS popularity,
        COALESCE(s.genres, []) AS spotify_genres,
        COALESCE(tg.mb_tags, []) AS mb_tags
    FROM `{S}.mb_artists` a
    LEFT JOIN `{S}.mbid_spotify_mapping` m USING (artist_mbid)
    LEFT JOIN `{P}.spotify_artists_normalized` s ON s.artist_id = m.spotify_artist_id
    LEFT JOIN (
        SELECT artist_mbid, ARRAY_AGG(tag_name ORDER BY vote_count DESC LIMIT 5) AS mb_tags
        FROM `{S}.mb_artist_tags`
        GROUP BY artist_mbid
    ) tg USING (artist_mbid)
    """,
    "mb_recordings_enriched": mb_recordings_enriched_sql(S, S),
    # Port of scripts/link_karaoke_to_recordings.py: ISRC chain first
    # (karaoke -> Spotify track by name -> ISRC -> MB recording), then exact
    # normalized name match to MB recordings for songs the ISRC pass missed.
    "karaoke_recording_links": f"""
    CREATE OR REPLACE TABLE `{S}.karaoke_recording_links` AS
    WITH normalized_karaoke AS (
        SELECT
            k.Id AS karaoke_id,
            norm(k.Artist) AS normalized_artist,
            norm(k.Title) AS normalized_title
        FROM `{P}.karaokenerds_raw` k
    ),
    spotify_matches AS (
        SELECT
            nk.karaoke_id,
            st.spotify_id,
            st.isrc,
            -- Deterministic tie-break: BigQuery may evaluate this CTE more than once
            -- (output + the NOT IN below), and both evaluations must agree.
            ROW_NUMBER() OVER (
                PARTITION BY nk.karaoke_id ORDER BY st.popularity DESC, st.spotify_id, st.isrc
            ) AS rn
        FROM normalized_karaoke nk
        JOIN `{P}.spotify_tracks` st
            ON norm(st.artist_name) = nk.normalized_artist
            AND norm(st.title) = nk.normalized_title
        WHERE st.isrc IS NOT NULL
    ),
    isrc_matches AS (
        SELECT
            sm.karaoke_id,
            sm.spotify_id AS spotify_track_id,
            ri.recording_mbid,
            ROW_NUMBER() OVER (PARTITION BY sm.karaoke_id ORDER BY ri.recording_mbid) AS rn
        FROM spotify_matches sm
        JOIN `{S}.mb_recording_isrc` ri ON sm.isrc = ri.isrc
        WHERE sm.rn = 1
    ),
    isrc_links AS (
        SELECT karaoke_id, recording_mbid, spotify_track_id
        FROM isrc_matches
        WHERE rn = 1
    ),
    name_matches AS (
        SELECT
            nk.karaoke_id,
            r.recording_mbid,
            ROW_NUMBER() OVER (PARTITION BY nk.karaoke_id ORDER BY r.recording_mbid) AS rn
        FROM normalized_karaoke nk
        JOIN `{S}.mb_recordings` r
            ON r.name_normalized = nk.normalized_title
            AND norm(r.artist_credit) = nk.normalized_artist
        WHERE nk.karaoke_id NOT IN (SELECT karaoke_id FROM isrc_links)
    )
    SELECT karaoke_id, recording_mbid, spotify_track_id,
           'isrc' AS match_method, 0.95 AS match_confidence
    FROM isrc_links
    UNION ALL
    SELECT karaoke_id, recording_mbid, CAST(NULL AS STRING),
           'exact_name', 0.80
    FROM name_matches
    WHERE rn = 1
    """,
}

MODEL_ORDER: list[str] = list(MODELS)


def model_script(name: str) -> str:
    """Full multi-statement script (temp functions + CTAS) for one model."""
    return PG_DECODE_FN + NORM_FN + MODELS[name]


# Row-count bounds relative to the current prod table: (min_ratio, max_ratio).
# MusicBrainz grows ~0.2%/week and almost never shrinks, so a big drop means a
# broken extract/load and a big jump means a parsing bug. None = no prod
# baseline yet (new table): only require it to be non-empty.
ROW_COUNT_BOUNDS: dict[str, tuple[float, float] | None] = {
    "mb_artists": (0.98, 1.25),
    "mb_artist_tags": (0.95, 1.30),
    "mb_recordings": (0.98, 1.25),
    "mb_recording_isrc": (0.98, 1.30),
    # First dump-built mapping covers every artist, not just the MLHD subset.
    "mbid_spotify_mapping": (0.90, 20.0),
    "mb_artist_redirects": None,
    "mb_recording_redirects": None,
    "mb_artists_normalized": (0.98, 1.25),
    "mb_recordings_enriched": (0.98, 1.25),
    "karaoke_recording_links": (0.97, 1.40),
}

# Sanity checks against staging. Each query returns one BOOL column ``ok`` and
# may return a ``detail`` column that's logged on failure.
CANARY_CHECKS: dict[str, str] = {
    "radiohead_artist": f"""
        SELECT COUNT(*) = 1 AS ok, TO_JSON_STRING(ARRAY_AGG(STRUCT(artist_name, spotify_artist_id))) AS detail
        FROM `{S}.mb_artists_normalized`
        WHERE artist_mbid = 'a74b1b7f-71a5-4011-9441-d0b5e4122711'
          AND artist_name = 'Radiohead'
          AND spotify_artist_id = '4Z8W4fKeB5YxbusRsdQVPb'
          AND ARRAY_LENGTH(mb_tags) > 0
    """,
    "green_day_artist": f"""
        SELECT COUNT(*) = 1 AS ok, TO_JSON_STRING(ARRAY_AGG(STRUCT(artist_name, spotify_artist_id))) AS detail
        FROM `{S}.mb_artists_normalized`
        WHERE artist_mbid = '084308bd-1654-436f-ba03-df6697104e19'
          AND artist_name = 'Green Day'
          AND spotify_artist_id = '7oPftvlwr6VrsViSDV7fJY'
    """,
    "creep_recording": f"""
        SELECT COUNT(*) >= 1 AS ok, CAST(COUNT(*) AS STRING) AS detail
        FROM `{S}.mb_recordings_enriched`
        WHERE recording_mbid = '70595637-9310-45f2-a266-58f8de4874a7'
          AND title = 'Creep' AND artist_credit = 'Radiohead'
          AND spotify_track_id IS NOT NULL
    """,
    "creep_isrc": f"""
        SELECT COUNT(*) = 1 AS ok, CAST(COUNT(*) AS STRING) AS detail
        FROM `{S}.mb_recording_isrc`
        WHERE isrc = 'GBAYE9200070' AND recording_mbid = '70595637-9310-45f2-a266-58f8de4874a7'
    """,
    "karaoke_links_both_methods": f"""
        SELECT COUNTIF(match_method = 'isrc') > 50000 AND COUNTIF(match_method = 'exact_name') > 30000 AS ok,
               FORMAT('isrc=%d exact_name=%d', COUNTIF(match_method = 'isrc'), COUNTIF(match_method = 'exact_name')) AS detail
        FROM `{S}.karaoke_recording_links`
    """,
    "karaoke_links_unique": f"""
        SELECT COUNT(*) = COUNT(DISTINCT karaoke_id) AS ok,
               FORMAT('rows=%d distinct=%d', COUNT(*), COUNT(DISTINCT karaoke_id)) AS detail
        FROM `{S}.karaoke_recording_links`
    """,
    "artists_unique": f"""
        SELECT COUNT(*) = COUNT(DISTINCT artist_mbid) AS ok,
               FORMAT('rows=%d distinct=%d', COUNT(*), COUNT(DISTINCT artist_mbid)) AS detail
        FROM `{S}.mb_artists_normalized`
    """,
    # Same name, different normalization => SQL normalization drifted from
    # what's in prod (and from the Python matcher). Must be exactly zero.
    "artist_normalization_parity": f"""
        SELECT COUNT(*) = 0 AS ok, CAST(COUNT(*) AS STRING) AS detail
        FROM `{S}.mb_artists` s JOIN `{P}.mb_artists` p USING (artist_mbid)
        WHERE s.name = p.name AND s.name_normalized != p.name_normalized
    """,
    "recording_normalization_parity": f"""
        SELECT COUNT(*) = 0 AS ok, CAST(COUNT(*) AS STRING) AS detail
        FROM `{S}.mb_recordings` s JOIN `{P}.mb_recordings` p USING (recording_mbid)
        WHERE s.title = p.title AND s.name_normalized != p.name_normalized
    """,
    # Real renames are rare; mass changes mean an escape/decoding bug.
    "artist_name_churn": f"""
        SELECT COUNTIF(s.name != p.name) / COUNT(*) < 0.03 AS ok,
               FORMAT('%.4f', COUNTIF(s.name != p.name) / COUNT(*)) AS detail
        FROM `{S}.mb_artists` s JOIN `{P}.mb_artists` p USING (artist_mbid)
    """,
    "recording_title_churn": f"""
        SELECT COUNTIF(s.title != p.title) / COUNT(*) < 0.03 AS ok,
               FORMAT('%.4f', COUNTIF(s.title != p.title) / COUNT(*)) AS detail
        FROM `{S}.mb_recordings` s JOIN `{P}.mb_recordings` p USING (recording_mbid)
    """,
}
