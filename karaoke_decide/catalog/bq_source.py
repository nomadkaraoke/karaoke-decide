"""Read catalog facet data from our BigQuery mirrors and assemble ``CatalogRows``.

Offline job only (no request path). Never calls the live MusicBrainz API.

Joins are done in Python on the shared keys (``song_key`` / ``artist_key``), so
SQL stays simple and every query is capped with ``MAX_BYTES_CATALOG``.
Spotify audio features always come from ONE chosen track per karaoke_id
(``QUALIFY ROW_NUMBER()``), never from per-column aggregates, so mood/energy
describe a real recording.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from google.cloud import bigquery

from karaoke_decide.catalog.build import ArtistRow, CatalogRows, KnRow, SimilarRow, SongRow
from karaoke_decide.catalog.genres import map_genres
from karaoke_decide.catalog.keys import artist_key, match_key, song_key
from karaoke_decide.catalog.moods import mood_for
from karaoke_decide.services.bq_limits import MAX_BYTES_CATALOG

logger = logging.getLogger(__name__)

P = "nomadkaraoke.karaoke_decide"
MB = "nomadkaraoke.musicbrainz"

PAGE_SIZE = 50_000
CLASSIC_BRANDS = 20
NO_KARAOKE_PER_ARTIST = 10
NO_KARAOKE_CANDIDATES_PER_ARTIST = 20  # SQL over-fetches; Python drops songs already in KN
# ListenBrainz all-time listener counts are small (max ~10k, p99 ~117 in Oct 2026). A floor of
# 1000 kept ~3k recordings worldwide and yielded ~200 songs. 40 yields ~31k songs and keeps
# catalog.db under the 50 MB gz budget (KN songs alone are ~46.5 MB gz; 20 would give ~54k
# songs at ~51.8 MB gz).
NO_KARAOKE_MIN_LISTENERS = 40
SIMILAR_PER_ARTIST = 30

# Same normalisation as mb_*.name_normalized / artist_normalized (lowercase, non [a-z0-9] -> space).
_NORM_SQL = "TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER({col}), r'[^a-z0-9 ]', ' '), r' +', ' '))"
# _NORM_SQL of the title with every bracketed qualifier dropped ("22 (Taylor's Version)" -> "22").
_BASE_TITLE_SQL = _NORM_SQL.format(col="REGEXP_REPLACE({col}, r'[\\[(][^\\])]*[\\])]', '')")

KN_SQL = f"""
SELECT Id, Artist, Title, Brands,
       ARRAY_LENGTH(SPLIT(Brands, ',')) AS brand_count
FROM `{P}.karaokenerds_raw`
WHERE Artist IS NOT NULL AND Title IS NOT NULL
"""

SONG_FACETS_SQL = f"""
WITH links AS (
  SELECT karaoke_id, recording_mbid, spotify_track_id
  FROM `{P}.karaoke_recording_links`
),
best_track AS (
  -- ONE Spotify track per karaoke_id (most popular); its audio-feature row is the only one used.
  SELECT l.karaoke_id,
         t.popularity AS spotify_pop, t.explicit, t.duration_ms, t.artist_spotify_id,
         af.energy, af.valence, af.danceability, af.tempo
  FROM links l
  JOIN `{P}.spotify_tracks` t ON t.spotify_id = l.spotify_track_id
  LEFT JOIN `{P}.spotify_audio_features` af ON af.track_id = t.spotify_id
  WHERE l.spotify_track_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY l.karaoke_id
                             ORDER BY t.popularity DESC, af.track_id IS NULL, t.spotify_id) = 1
),
years AS (
  SELECT l.karaoke_id,
         COALESCE(
           MIN(IF(rg.primary_type IN ('Album', 'Single', 'EP') AND ARRAY_LENGTH(rg.secondary_types) = 0,
                  rg.first_release_year, NULL)),
           MIN(rg.first_release_year)
         ) AS year
  FROM links l
  JOIN `{P}.mb_tracks` mt ON mt.recording_mbid = l.recording_mbid
  JOIN `{P}.mb_release_groups` rg ON rg.release_group_mbid = mt.release_group_mbid
  WHERE rg.first_release_year > 0
  GROUP BY l.karaoke_id
),
listeners AS (
  SELECT l.karaoke_id, MAX(p.listeners) AS lb_listeners
  FROM links l
  JOIN `{P}.lb_recording_popularity` p
    ON p.recording_mbid = l.recording_mbid AND p.stats_range = 'all_time'
  GROUP BY l.karaoke_id
)
SELECT k.karaoke_id,
       b.energy, b.valence, b.danceability, b.tempo,
       b.explicit, b.duration_ms, b.spotify_pop, b.artist_spotify_id,
       y.year, lp.lb_listeners
FROM (SELECT DISTINCT karaoke_id FROM links) k
LEFT JOIN best_track b USING (karaoke_id)
LEFT JOIN years y USING (karaoke_id)
LEFT JOIN listeners lp USING (karaoke_id)
"""

NO_KARAOKE_SQL = f"""
WITH kn_norm AS (
  SELECT DISTINCT {_NORM_SQL.format(col="Artist")} AS artist_normalized,
                  {_BASE_TITLE_SQL.format(col="Title")} AS base_title
  FROM `{P}.karaokenerds_raw`
),
kn_artists AS (SELECT DISTINCT artist_normalized FROM kn_norm),
recordings AS (
  -- mb_recordings_enriched has one row per Spotify track: collapse to one row per recording.
  SELECT recording_mbid, ANY_VALUE(title) AS title, ANY_VALUE(artist_credit) AS artist,
         ANY_VALUE(artist_normalized) AS artist_normalized,
         MAX(length_ms) AS duration_ms, MAX(spotify_popularity) AS spotify_pop
  FROM `{P}.mb_recordings_enriched`
  WHERE artist_normalized IN (SELECT artist_normalized FROM kn_artists)
  GROUP BY recording_mbid
),
listened AS (
  SELECT r.recording_mbid, r.title, r.artist, r.artist_normalized, r.duration_ms, r.spotify_pop, p.listeners,
         {_BASE_TITLE_SQL.format(col="r.title")} AS base_title
  FROM `{P}.lb_recording_popularity` p
  JOIN recordings r ON r.recording_mbid = p.recording_mbid
  WHERE p.stats_range = 'all_time'
    AND p.listeners >= {NO_KARAOKE_MIN_LISTENERS}
    AND NOT REGEXP_CONTAINS(LOWER(r.title), r'\\blive\\b|remix|demo')
),
song_best AS (
  -- One row per song: the most-listened recording of each (artist, bracket-free title), so
  -- reissues, single/album duplicates and "(Taylor's Version)" re-recordings don't use up an
  -- artist's candidate slots. A song is "in KN" when its bracket-free title matches a KN one.
  SELECT l.* EXCEPT (base_title)
  FROM listened l
  WHERE NOT EXISTS (SELECT 1 FROM kn_norm k
                    WHERE k.artist_normalized = l.artist_normalized AND k.base_title = l.base_title)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY l.artist_normalized, l.base_title
                             ORDER BY l.listeners DESC, l.recording_mbid) = 1
),
popular AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY artist_normalized ORDER BY listeners DESC, recording_mbid) AS rn
  FROM song_best
),
years AS (
  SELECT mt.recording_mbid, MIN(rg.first_release_year) AS year
  FROM `{P}.mb_tracks` mt
  JOIN `{P}.mb_release_groups` rg ON rg.release_group_mbid = mt.release_group_mbid
  WHERE rg.first_release_year > 0
    AND rg.primary_type IN ('Album', 'Single', 'EP') AND ARRAY_LENGTH(rg.secondary_types) = 0
  GROUP BY mt.recording_mbid
)
SELECT pp.recording_mbid, pp.title, pp.artist, pp.duration_ms, pp.spotify_pop, pp.listeners, y.year
FROM popular pp
LEFT JOIN years y ON y.recording_mbid = pp.recording_mbid
WHERE pp.rn <= {NO_KARAOKE_CANDIDATES_PER_ARTIST}
"""

ARTISTS_SQL = f"""
WITH leads AS (
  -- Gender of the band members credited with lead vocals ("member of band" + attribute "lead vocals").
  SELECT band.gid AS band_mbid,
         ARRAY_AGG(DISTINCT LOWER(g.name)) AS genders
  FROM `{MB}.l_artist_artist` laa
  JOIN `{MB}.link` lk ON lk.id = laa.link
  JOIN `{MB}.link_type` lt ON lt.id = lk.link_type AND lt.name = 'member of band'
  JOIN `{MB}.link_attribute` la ON la.link = lk.id
  JOIN `{MB}.link_attribute_type` lat ON lat.id = la.attribute_type AND lat.name = 'lead vocals'
  JOIN `{MB}.artist` member ON member.id = laa.entity0
  JOIN `{MB}.artist` band ON band.id = laa.entity1
  JOIN `{MB}.gender` g ON g.id = member.gender AND g.name IN ('Male', 'Female')
  GROUP BY band.gid
)
SELECT a.artist_mbid AS mbid, a.artist_name AS name, a.popularity,
       a.spotify_genres, a.mb_tags, lp.listeners AS lb_listeners,
       CASE
         WHEN a.artist_type = 'Person' THEN
           (SELECT LOWER(g.name) FROM `{MB}.artist` pa JOIN `{MB}.gender` g ON g.id = pa.gender
            WHERE pa.gid = a.artist_mbid AND g.name IN ('Male', 'Female'))
         WHEN a.artist_type = 'Group' THEN
           CASE ARRAY_LENGTH(l.genders) WHEN 1 THEN l.genders[OFFSET(0)] WHEN 2 THEN 'mixed' END
       END AS gender
FROM `{P}.mb_artists_normalized` a
LEFT JOIN leads l ON l.band_mbid = a.artist_mbid
LEFT JOIN `{P}.lb_artist_popularity` lp
  ON lp.artist_mbid = a.artist_mbid AND lp.stats_range = 'all_time'
WHERE a.artist_name IS NOT NULL
  AND a.name_normalized IN (SELECT {_NORM_SQL.format(col="Artist")} FROM `{P}.karaokenerds_raw`)
"""

SIMILAR_SQL = f"""
SELECT artist_a_name, artist_b_name, jaccard_similarity
FROM `{P}.mlhd_artist_similarity`
WHERE artist_a_name IS NOT NULL AND artist_b_name IS NOT NULL AND artist_a_name != artist_b_name
QUALIFY ROW_NUMBER() OVER (PARTITION BY artist_a_name ORDER BY jaccard_similarity DESC, artist_b_name) <= {SIMILAR_PER_ARTIST}
"""

RAW_GENRES_SQL = f"""
SELECT genre FROM `{P}.spotify_artist_genres` WHERE genre IS NOT NULL
UNION DISTINCT
SELECT tag_name AS genre FROM `{P}.mb_artist_tags` WHERE vote_count >= 1 AND tag_name IS NOT NULL
"""


def _run(bq: bigquery.Client, sql: str, *, dry_run: bool) -> Iterator[dict[str, Any]]:
    """Run one capped query and stream its rows as dicts (nothing when ``dry_run``)."""
    config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_CATALOG, dry_run=dry_run, use_query_cache=True)
    job = bq.query(sql, job_config=config)
    if dry_run:
        logger.info("catalog dry run: %.2f GiB", (job.total_bytes_processed or 0) / 2**30)
        return
    for row in job.result(page_size=PAGE_SIZE):
        yield dict(row)


def _decade(year: int | None) -> str | None:
    return f"{year // 10 * 10}s" if year else None


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _explicit(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _artist_rows(rows: Iterator[dict[str, Any]]) -> list[ArtistRow]:
    artists: list[ArtistRow] = []
    for r in rows:
        key = artist_key(r["name"])
        if not key:
            continue
        genres = map_genres([*(r.get("spotify_genres") or []), *(r.get("mb_tags") or [])])
        artists.append(
            ArtistRow(
                artist_key=key,
                name=r["name"],
                mbid=r.get("mbid"),
                lb_listeners=_as_int(r.get("lb_listeners")),
                popularity=_as_int(r.get("popularity")),
                gender=r.get("gender"),
                genres=tuple(genres),
            )
        )
    return artists


def _kn_songs(kn: list[KnRow], facets: dict[int, dict[str, Any]], artists: dict[str, ArtistRow]) -> list[SongRow]:
    """One SongRow per song_key: the KN row with most brands, facets from the first linked row."""
    groups: dict[str, list[KnRow]] = defaultdict(list)
    for row in kn:
        groups[row.song_key].append(row)
    songs: list[SongRow] = []
    for key, group in groups.items():
        group.sort(key=lambda k: (-k.brand_count, k.kn_id))
        head = group[0]
        f = next((facets[k.kn_id] for k in group if k.kn_id in facets), {})
        a_key = artist_key(head.artist)
        artist = artists.get(a_key)
        energy, valence = _as_float(f.get("energy")), _as_float(f.get("valence"))
        year = _as_int(f.get("year"))
        songs.append(
            SongRow(
                song_key=key,
                artist=head.artist,
                title=head.title,
                artist_key=a_key,
                brand_count=head.brand_count,
                is_classic=int(head.brand_count >= CLASSIC_BRANDS),
                explicit=_explicit(f.get("explicit")),
                duration_ms=_as_int(f.get("duration_ms")),
                year=year,
                decade=_decade(year),
                lb_listeners=_as_int(f.get("lb_listeners")),
                spotify_pop=_as_int(f.get("spotify_pop")),
                energy=energy,
                valence=valence,
                danceability=_as_float(f.get("danceability")),
                tempo=_as_float(f.get("tempo")),
                mood=mood_for(valence, energy),
                lead_gender=artist.gender if artist else None,
                genres=artist.genres if artist else (),
                in_kn=1,
            )
        )
    return songs


_BRACKETED_RE = re.compile(r"\s*[\[(][^\])]*[\])]")


def _base_key(artist: str, title: str) -> str:
    """song_key with every bracketed qualifier dropped: "22 (Taylor's Version)" -> "22"."""
    return song_key(artist, _BRACKETED_RE.sub("", title).strip() or title)


def _kn_title_keys(kn: list[KnRow]) -> set[str]:
    """Exact and bracket-free song keys of every KN song (what counts as "has a karaoke version")."""
    return {k.song_key for k in kn} | {_base_key(k.artist, k.title) for k in kn}


# Album filler that nobody sings at karaoke (whole-word match, so "Introspection" survives).
_NON_SONG_TITLE_RE = re.compile(r"\b(interlude|prelude|intro|outro|skit)\b", re.IGNORECASE)


def _no_karaoke_songs(
    rows: Iterator[dict[str, Any]], kn_keys: set[str], artists: dict[str, ArtistRow]
) -> list[SongRow]:
    """Top listened songs per KN artist that have no KN version (in_kn = 0).

    ``kn_keys`` comes from ``_kn_title_keys``: a candidate is skipped when its exact or
    bracket-free key matches a KN song, so "22 (Taylor's Version)" doesn't duplicate KN's "22".
    """
    candidates: dict[str, list[SongRow]] = defaultdict(list)
    seen: set[str] = set()
    for r in sorted(rows, key=lambda r: -(r.get("listeners") or 0)):
        if _NON_SONG_TITLE_RE.search(r["title"]):
            continue
        key = song_key(r["artist"], r["title"])
        base = _base_key(r["artist"], r["title"])
        a_key = artist_key(r["artist"])
        if key in kn_keys or base in kn_keys or len(candidates[a_key]) >= NO_KARAOKE_PER_ARTIST:
            continue
        if key in seen or base in seen:
            continue
        seen.update((key, base))
        artist = artists.get(a_key)
        year = _as_int(r.get("year"))
        candidates[a_key].append(
            SongRow(
                song_key=key,
                artist=r["artist"],
                title=r["title"],
                artist_key=a_key,
                duration_ms=_as_int(r.get("duration_ms")),
                year=year,
                decade=_decade(year),
                lb_listeners=_as_int(r.get("listeners")),
                spotify_pop=_as_int(r.get("spotify_pop")),
                lead_gender=artist.gender if artist else None,
                genres=artist.genres if artist else (),
                in_kn=0,
            )
        )
    return [s for group in candidates.values() for s in group]


def _similar_rows(rows: Iterator[dict[str, Any]], wanted: set[str]) -> list[SimilarRow]:
    """MLHD pairs for catalog artists; score = jaccard / that artist's max jaccard (0-1)."""
    best: dict[tuple[str, str], float] = {}
    for r in rows:
        a, b = artist_key(r["artist_a_name"]), artist_key(r["artist_b_name"])
        if not a or not b or a == b or a not in wanted:
            continue
        score = float(r["jaccard_similarity"] or 0.0)
        best[(a, b)] = max(score, best.get((a, b), 0.0))
    peak: dict[str, float] = defaultdict(float)
    for (a, _), score in best.items():
        peak[a] = max(peak[a], score)
    return [SimilarRow(a, b, score / peak[a] if peak[a] else 0.0, "mlhd") for (a, b), score in best.items()]


def fetch_catalog_rows(bq: bigquery.Client, *, dry_run: bool = False) -> CatalogRows:
    """Run every catalog query and assemble the rows for ``build_catalog_db``.

    With ``dry_run`` the queries are only validated/estimated and empty rows are returned.
    """
    kn = [
        KnRow(
            kn_id=int(r["Id"]),
            song_key=song_key(r["Artist"], r["Title"]),
            artist=r["Artist"],
            title=r["Title"],
            brands=r["Brands"] or "",
            brand_count=int(r["brand_count"] or 0),
            match_key=match_key(r["Artist"], r["Title"]),
        )
        for r in _run(bq, KN_SQL, dry_run=dry_run)
    ]
    facets = {int(r["karaoke_id"]): r for r in _run(bq, SONG_FACETS_SQL, dry_run=dry_run)}
    artist_rows = _artist_rows(_run(bq, ARTISTS_SQL, dry_run=dry_run))
    artists = {
        a.artist_key: a for a in sorted(artist_rows, key=lambda a: (a.lb_listeners or -1, a.mbid or ""))
    }  # best listened wins

    kn_songs = _kn_songs(kn, facets, artists)
    extra = _no_karaoke_songs(_run(bq, NO_KARAOKE_SQL, dry_run=dry_run), _kn_title_keys(kn), artists)
    songs = [*kn_songs, *extra]

    wanted = {s.artist_key for s in songs}
    similar = _similar_rows(_run(bq, SIMILAR_SQL, dry_run=dry_run), wanted)
    genre_map: dict[str, str] = {}
    for r in _run(bq, RAW_GENRES_SQL, dry_run=dry_run):
        mapped = map_genres([r["genre"]])
        if mapped:
            genre_map[r["genre"]] = mapped[0]
    return CatalogRows(
        songs=songs,
        artists=[a for a in artists.values() if a.artist_key in wanted],
        similar=similar,
        kn=kn,
        genre_map=genre_map,
    )
