"""Voice-pool selection job: which songs the voice session can play, and their best version.

1. Candidates (``load_candidates``): catalog.db songs in KaraokeNerds (``in_kn``) with at least
   one community version that has a YouTube id in availability.db (``av.versions``), a Spotify
   track 120-420 s long (BigQuery, the same one-track-per-karaoke_id rule as the catalog's
   facets), and ``popularity >= min_pop``; ``min_pop`` steps down by 5 to 45 until there are at
   least 3 x the target. ``has_ultrastar`` comes from ``song_vocal_range_sources``.
2. ``strata.select_pool`` balances the pick; each picked song's versions are probed with
   YouTube oEmbed (best first, stopping at the first that isn't known non-embeddable). A song
   whose every version is non-embeddable is dropped and the pick is redone without it.
3. Unless ``dry_run``: the songs join the fetch queue (``queue.enqueue``, ``source='shared'``),
   one row each is appended to BigQuery ``voice_pool_songs``, and the coverage report is
   written to ``gs://nomadkaraoke-musicbrainz-data/voice/selection/<run>/coverage.json``.

KaraokeNerds data comes only from our own copies (catalog.db / availability.db / BigQuery).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from karaoke_decide.catalog.bq_source import P
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.voice_pool import queue
from karaoke_decide.voice_pool.models import PoolCandidate, Version, pool_id
from karaoke_decide.voice_pool.spotify import SpotifyTrack, spotify_tracks
from karaoke_decide.voice_pool.spotify import bq_query as _query
from karaoke_decide.voice_pool.strata import MIN_POOL, coverage, select_pool
from karaoke_decide.voice_pool.versions import (
    OEMBED_LIMITER,
    RateLimiter,
    choose_versions,
    community_versions,
    probe_embeddable,
)

logger = logging.getLogger(__name__)

DEFAULT_MIN_POP = 60
MIN_POP_FLOOR = 45
MIN_POP_STEP = 5
CANDIDATE_FACTOR = 3
MIN_DURATION_MS = 120_000
MAX_DURATION_MS = 420_000
UNKNOWN_PROBE_WARN_FRACTION = 0.5  # more unknown oEmbed answers than this: YouTube is likely throttling us

BUCKET = "nomadkaraoke-musicbrainz-data"
POOL_TABLE = f"{P}.voice_pool_songs"

ULTRASTAR_SQL = f"""
SELECT DISTINCT song_key FROM `{P}.song_vocal_range_sources`
WHERE source = 'ultrastar' AND song_key IN UNNEST(@song_keys)
"""

POOL_SCHEMA = [
    bigquery.SchemaField(name, kind, mode="NULLABLE")
    for name, kind in (
        ("song_key", "STRING"),
        ("pool_id", "STRING"),
        ("artist", "STRING"),
        ("title", "STRING"),
        ("source", "STRING"),
        ("stratum", "STRING"),
        ("spotify_id", "STRING"),
        ("duration_ms", "INT64"),
        ("video_id", "STRING"),
        ("brand_code", "STRING"),
        ("selection_run", "STRING"),
        ("added_at", "TIMESTAMP"),
    )
]

_VR_COLUMNS = ("vr_low", "vr_high", "vr_p50", "vr_tier", "vr_duet")
_SONGS_SQL = """
SELECT song_key, artist, title, artist_key, popularity, lead_gender, decade, energy,
       {vr}, duration_ms
FROM songs
WHERE in_kn = 1 AND popularity >= ?
"""
VR_MISSING_MSG = (
    "catalog.db predates vocal-range data (songs.vr_* columns missing, added by schema 2): "
    "register/span strata will be 'unknown'"
)


class VocalRangeMissingError(RuntimeError):
    """The catalog has no songs.vr_* columns and the caller required them."""


_VERSIONS_SQL = """
SELECT v.song_key, v.brand_code, v.brand_name, v.youtube_url
FROM av.versions v JOIN songs s ON s.song_key = v.song_key
WHERE s.in_kn = 1 AND s.popularity >= ? AND v.kind = 'community' AND v.youtube_url IS NOT NULL
ORDER BY v.song_key, v.rank, v.rowid
"""
_KN_IDS_SQL = """
SELECT k.kn_id, k.song_key FROM kn_song_map k JOIN songs s ON s.song_key = k.song_key
WHERE s.in_kn = 1 AND s.popularity >= ?
"""


# ------------------------------------------------------------------ candidates


def _decade(value: str | None) -> str | None:
    """catalog.db "1970s" -> the sampler's buckets ("pre1980" ... "2010s+")."""
    try:
        year = int((value or "")[:4])
    except ValueError:
        return None
    if year < 1980:
        return "pre1980"
    if year >= 2010:
        return "2010s+"
    return f"{year}s"


def _community_versions(reader: CatalogReader, floor: int) -> dict[str, tuple[Version, ...]]:
    """``{song_key: versions}``: community versions with a YouTube id, ``rank`` = 0-based
    position in availability.db's own order (one entry per video)."""
    return community_versions(reader.conn().execute(_VERSIONS_SQL, (floor,)))


def _ultrastar_keys(bq: Any, song_keys: list[str]) -> set[str]:
    if bq is None or not song_keys:
        return set()
    try:
        rows = _query(bq, ULTRASTAR_SQL, [bigquery.ArrayQueryParameter("song_keys", "STRING", sorted(song_keys))])
    except NotFound:
        logger.warning("song_vocal_range_sources not found: has_ultrastar is False for every candidate")
        return set()
    return {r["song_key"] for r in rows}


def _vr_span(low: float | None, high: float | None) -> float | None:
    return None if low is None or high is None else float(high) - float(low)


def _songs_sql(conn: Any, require_vocal_range: bool) -> str:
    """``_SONGS_SQL`` with NULLs for the vr_* columns an older catalog.db lacks (one WARNING)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(songs)")}
    if all(c in have for c in _VR_COLUMNS):
        return _SONGS_SQL.format(vr=", ".join(_VR_COLUMNS))
    logger.warning(VR_MISSING_MSG)
    if require_vocal_range:
        raise VocalRangeMissingError(VR_MISSING_MSG)
    return _SONGS_SQL.format(vr=", ".join(c if c in have else f"NULL AS {c}" for c in _VR_COLUMNS))


def load_candidates(
    reader: CatalogReader,
    bq: Any,
    *,
    min_pop: int = DEFAULT_MIN_POP,
    n: int = 1000,
    require_vocal_range: bool = False,
) -> list[PoolCandidate]:
    """Eligible pool songs (see module doc), most popular first; ``n`` is the pool target.

    ``bq=None`` (local dev, dry runs only): no Spotify ids or UltraStar flags, catalog durations."""
    floor = min(min_pop, MIN_POP_FLOOR)
    conn = reader.conn()
    versions = _community_versions(reader, floor)
    songs = [r for r in conn.execute(_songs_sql(conn, require_vocal_range), (floor,)) if r["song_key"] in versions]
    kn_by_song: dict[str, list[int]] = {}
    for kn, key in conn.execute(_KN_IDS_SQL, (floor,)):
        if key in versions:
            kn_by_song.setdefault(key, []).append(int(kn))
    tracks: dict[str, SpotifyTrack]
    if bq is None:
        # Local dev only (BIGQUERY_DISABLED): catalog.db's duration (the same Spotify track), no ids.
        logger.warning("BigQuery disabled: no Spotify ids or UltraStar flags; durations from catalog.db")
        tracks = {r["song_key"]: (None, r["duration_ms"]) for r in songs}
    else:
        tracks = spotify_tracks(bq, kn_by_song)
    songs = [
        r
        for r in songs
        if r["song_key"] in tracks and MIN_DURATION_MS <= (tracks[r["song_key"]][1] or 0) <= MAX_DURATION_MS
    ]
    ultrastar = _ultrastar_keys(bq, [r["song_key"] for r in songs])

    eligible = [
        PoolCandidate(
            song_key=r["song_key"],
            artist=r["artist"],
            title=r["title"],
            artist_key=r["artist_key"],
            popularity=int(r["popularity"] or 0),
            lead_gender=r["lead_gender"],
            decade=_decade(r["decade"]),
            energy=r["energy"],
            duet=bool(r["vr_duet"]),
            vr_p50=r["vr_p50"],
            vr_span=_vr_span(r["vr_low"], r["vr_high"]),
            vr_tier=r["vr_tier"],
            has_ultrastar=r["song_key"] in ultrastar,
            spotify_id=tracks[r["song_key"]][0],
            duration_ms=tracks[r["song_key"]][1],
            versions=versions[r["song_key"]],
        )
        for r in songs
    ]
    threshold = min_pop
    want = CANDIDATE_FACTOR * n
    while threshold > floor and sum(c.popularity >= threshold for c in eligible) < want:
        threshold = max(floor, threshold - MIN_POP_STEP)
    out = sorted((c for c in eligible if c.popularity >= threshold), key=lambda c: (-c.popularity, c.song_key))
    logger.info("voice pool: %d candidates at min_pop %d (target %d, want %d)", len(out), threshold, n, want)
    return out


# ------------------------------------------------------------------ selection


def _dedupe(cands: list[PoolCandidate]) -> list[PoolCandidate]:
    seen: set[str] = set()
    out: list[PoolCandidate] = []
    for c in cands:
        if c.song_key not in seen:
            seen.add(c.song_key)
            out.append(c)
    return out


class _Prober:
    """Probes a song's versions best first until one isn't known non-embeddable; caches per video."""

    def __init__(self, http: httpx.Client | None, limiter: RateLimiter, enabled: bool) -> None:
        self.http = http
        self.limiter = limiter
        self.enabled = enabled and http is not None
        self.results: dict[str, bool | None] = {}

    def versions(self, c: PoolCandidate) -> list[Version] | None:
        """``c``'s versions best first with what we learned, or None when every one is non-embeddable."""
        ordered = choose_versions(list(c.versions))
        if not self.enabled:
            return ordered
        assert self.http is not None
        for v in ordered:
            if v.video_id not in self.results:
                self.results[v.video_id] = probe_embeddable(self.http, v.video_id, limiter=self.limiter)
            if self.results[v.video_id] is not False:
                break
        known = [dataclasses.replace(v, embeddable=self.results.get(v.video_id, v.embeddable)) for v in ordered]
        if all(v.embeddable is False for v in known):
            return None
        return choose_versions(known)


def _pick(
    cands: list[PoolCandidate], n: int, prober: _Prober
) -> tuple[list[tuple[PoolCandidate, str, list[Version]]], set[str]]:
    """Balanced pick of ``n`` with every all-non-embeddable song replaced; returns (picks, dropped)."""
    dropped: set[str] = set()
    while True:
        picked = select_pool([c for c in cands if c.song_key not in dropped], n)
        out: list[tuple[PoolCandidate, str, list[Version]]] = []
        newly: set[str] = set()
        for c, stratum in picked:
            versions = prober.versions(c)
            if versions is None:
                newly.add(c.song_key)
            else:
                out.append((c, stratum, versions))
        if not newly:
            return out, dropped
        dropped |= newly


def _pool_rows(picks: list[tuple[PoolCandidate, str, list[Version]]], run: str, now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "song_key": c.song_key,
            "pool_id": pool_id(c.song_key),
            "artist": c.artist,
            "title": c.title,
            "source": "shared",
            "stratum": stratum,
            "spotify_id": c.spotify_id,
            "duration_ms": c.duration_ms,
            "video_id": versions[0].video_id,
            "brand_code": versions[0].brand_code,
            "selection_run": run,
            "added_at": now.isoformat(),
        }
        for c, stratum, versions in picks
    ]


def _append_pool_rows(bq: Any, rows: list[dict[str, Any]]) -> None:
    config = bigquery.LoadJobConfig(
        schema=POOL_SCHEMA,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    bq.load_table_from_json(rows, POOL_TABLE, job_config=config).result()
    logger.info("appended %d rows to %s", len(rows), POOL_TABLE)


def _write_coverage(gcs: Any, run: str, summary: dict[str, Any]) -> str:
    name = f"voice/selection/{run}/coverage.json"
    with gcs.bucket(BUCKET).blob(name).open("wb", content_type="application/json") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True).encode())
    return f"gs://{BUCKET}/{name}"


async def run_select(
    reader: CatalogReader,
    bq: Any,
    fs: Any,
    http: httpx.Client | None,
    *,
    n: int = 1000,
    run: str,
    dry_run: bool,
    gcs: Any = None,
    probe: bool = True,
    limiter: RateLimiter = OEMBED_LIMITER,
    require_vocal_range: bool = False,
) -> dict[str, Any]:
    """Select the pool, probe embeddability, and (unless ``dry_run``) queue + record it.

    Returns the ``CoverageReport`` as a dict plus ``candidates``, ``probed`` (oEmbed requests),
    ``probe_unknown`` (requests that answered neither yes nor no), ``non_embeddable`` (songs
    dropped because no version can be embedded), ``enqueued``,
    ``songs`` (picked song keys, pick order), ``run`` and ``dry_run``. ``probe=False`` skips oEmbed.
    """
    cands = _dedupe(load_candidates(reader, bq, n=n, require_vocal_range=require_vocal_range))
    prober = _Prober(http, limiter, probe)
    picks, dropped = _pick(cands, n, prober)
    report = coverage([(c, stratum) for c, stratum, _ in picks], n)
    summary: dict[str, Any] = {
        **dataclasses.asdict(report),
        "candidates": len(cands),
        "probed": len(prober.results),
        "probe_unknown": sum(v is None for v in prober.results.values()),
        "non_embeddable": len(dropped),
        "enqueued": 0,
        "songs": [c.song_key for c, _, _ in picks],
        "run": run,
        "dry_run": dry_run,
    }
    if summary["probed"] and summary["probe_unknown"] > UNKNOWN_PROBE_WARN_FRACTION * summary["probed"]:
        logger.warning(
            "voice pool %s: %d of %d oEmbed probes were unknown; YouTube may be rate-limiting or blocking us",
            run,
            summary["probe_unknown"],
            summary["probed"],
        )
    if report.below_minimum:
        logger.warning("voice pool %s: only %d songs picked (minimum %d)", run, report.picked, MIN_POOL)
    if not dry_run:
        items = [
            {
                "song_key": c.song_key,
                "artist": c.artist,
                "title": c.title,
                "source": "shared",
                "spotify_id": c.spotify_id,
                "duration_ms": c.duration_ms,
                "stratum": stratum,
                "versions_embeddable": {v.video_id: v.embeddable for v in versions if v.embeddable is not None},
            }
            for c, stratum, versions in picks
        ]
        summary["enqueued"] = await queue.enqueue(fs, items)
        _append_pool_rows(bq, _pool_rows(picks, run, datetime.now(UTC)))
        summary["coverage_uri"] = _write_coverage(gcs, run, summary)
    logger.info(
        "voice pool %s: picked %d/%d from %d candidates; probed %d (%d unknown), dropped %d non-embeddable,"
        " enqueued %d%s",
        run,
        report.picked,
        n,
        len(cands),
        summary["probed"],
        summary["probe_unknown"],
        len(dropped),
        summary["enqueued"],
        " (dry run)" if dry_run else "",
    )
    return summary
