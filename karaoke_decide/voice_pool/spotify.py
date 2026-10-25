"""The Spotify track behind a voice-pool song: ``(spotify_id, duration_ms)`` from its KaraokeNerds ids.

Shared by the selection job (``select.load_candidates``, every candidate in one query) and the
fetch job (``fetch.resolve_missing``: queued songs enqueued without an id, e.g. Andrew's sheet or a
personal pool). catalog.db ``kn_song_map`` gives a song's KaraokeNerds ids; BigQuery gives the
Spotify track linked to each, one per karaoke_id exactly as catalog/bq_source.SONG_FACETS_SQL
picks it; across a song's ids the most popular track wins.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from google.cloud import bigquery

from karaoke_decide.catalog.bq_source import P
from karaoke_decide.services.bq_limits import MAX_BYTES_CATALOG

SpotifyTrack = tuple[str | None, int | None]

SPOTIFY_FOR_KN_SQL = f"""
SELECT l.karaoke_id, t.spotify_id, t.duration_ms, t.popularity AS spotify_pop
FROM `{P}.karaoke_recording_links` l
JOIN `{P}.spotify_tracks` t ON t.spotify_id = l.spotify_track_id
LEFT JOIN `{P}.spotify_audio_features` af ON af.track_id = t.spotify_id
WHERE l.spotify_track_id IS NOT NULL AND l.karaoke_id IN UNNEST(@kn_ids)
QUALIFY ROW_NUMBER() OVER (PARTITION BY l.karaoke_id
                           ORDER BY t.popularity DESC, af.track_id IS NULL, t.spotify_id) = 1
"""


def bq_query(bq: Any, sql: str, params: list[bigquery.ArrayQueryParameter]) -> list[dict[str, Any]]:
    """``sql`` with ``params`` under the catalog byte cap; rows as dicts."""
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_CATALOG, query_parameters=params, use_query_cache=True
    )
    return [dict(r) for r in bq.query(sql, job_config=config).result()]


def spotify_tracks(bq: Any, kn_by_song: dict[str, list[int]]) -> dict[str, SpotifyTrack]:
    """``{song_key: (spotify_id, duration_ms)}``: across a song's KaraokeNerds ids, the most
    popular linked track (ties: spotify_id). One query; none when there are no ids."""
    kn_ids = sorted({kn for ids in kn_by_song.values() for kn in ids})
    if not kn_ids:
        return {}
    rows = bq_query(bq, SPOTIFY_FOR_KN_SQL, [bigquery.ArrayQueryParameter("kn_ids", "INT64", kn_ids)])
    by_kn = {int(r["karaoke_id"]): r for r in rows if r.get("spotify_id")}
    out: dict[str, SpotifyTrack] = {}
    for key, ids in kn_by_song.items():
        tracks = [by_kn[kn] for kn in ids if kn in by_kn]
        if tracks:
            best = min(tracks, key=lambda t: (-(t.get("spotify_pop") or 0), t["spotify_id"]))
            dur = best.get("duration_ms")
            out[key] = (best["spotify_id"], None if dur is None else int(dur))
    return out


def resolve_song_keys(reader: Any, bq: Any, song_keys: Sequence[str]) -> dict[str, SpotifyTrack]:
    """``{song_key: (spotify_id, duration_ms)}`` for the ``song_keys`` that have a linked track
    (``reader`` is a ``CatalogReader``; at most one BigQuery query)."""
    return spotify_tracks(bq, reader.all_kn_ids_for_song_keys(song_keys))
