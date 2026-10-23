"""voice.db: the small SQLite file the API's voice session plays from, behind ``voice/latest.json``.

Built from the fetch queue (every song except ``failed``; analysed songs carry their markers as
the queue doc's ``markers``, and the selection's oEmbed verdicts as ``versions_embeddable``), the
catalog (artist key, popularity, lead gender, and the song's community versions from
availability.db) and Firestore ``voice_version_stats`` (singer feedback; its ``embeddable``
verdict beats the probe's).
BigQuery ``song_voice_analysis`` stays the source of record; the queue holds the same rows.

``pool`` lists every queued song (fetching, separating and analysed alike), so it is larger than
what a session can play. Session candidates are ``pool JOIN analysis`` (on ``song_key``): only
songs with markers, which excludes songs still in the pipeline and ``too_little_voice`` ones.

Published to ``gs://nomadkaraoke-musicbrainz-data/voice/<run>/voice.db.gz`` (gzip mtime 0), then
``voice/latest.json`` = ``{run, sha256, songs, analysed, built_at}`` LAST (``sha256`` of the
uncompressed db, as for catalog.db), then only the newest ``KEEP_RUNS`` ``voice/<run>/`` dirs are
kept (``voice/selection/`` and everything else under ``voice/`` are never touched).

Imports no numpy / librosa: the main API image and the local stack use this module.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.voice_pool.models import Version
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION
from karaoke_decide.voice_pool.versions import VersionStats, community_versions, version_quality

logger = logging.getLogger(__name__)

VOICE_BUCKET = "nomadkaraoke-musicbrainz-data"
VOICE_PREFIX = "voice"
VOICE_LATEST = f"{VOICE_PREFIX}/latest.json"
KEEP_RUNS = 3
SCHEMA_VERSION = 1
# voice.db's analysis_version meta; kept in step with voice_analysis.markers.ANALYSIS_VERSION
# (asserted by the tests; not imported, to keep numpy out of this module).
ANALYSIS_VERSION = 1
VERSION_STATS_COLLECTION = "voice_version_stats"
_RUN_DIR = re.compile(rf"^{VOICE_PREFIX}/(\d{{8}}-\d{{6}})/")
_CHUNK = 500  # SQLite host parameters per IN (...)

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE pool (
    song_key TEXT PRIMARY KEY,
    pool_id TEXT,
    artist TEXT,
    title TEXT,
    artist_key TEXT,
    source TEXT,
    stratum TEXT,
    spotify_id TEXT,
    duration_ms INTEGER,
    popularity INTEGER,
    lead_gender TEXT
);
CREATE TABLE versions (
    song_key TEXT,
    video_id TEXT,
    brand_code TEXT,
    brand_name TEXT,
    rank INTEGER,
    embeddable INTEGER NULL,
    quality REAL,
    PRIMARY KEY (song_key, video_id)
);
CREATE TABLE analysis (
    song_key TEXT PRIMARY KEY,
    analysis_version INTEGER,
    low REAL,
    high REAL,
    p10 REAL,
    p50 REAL,
    p90 REAL,
    voiced_s REAL,
    secs_above_json TEXT,
    secs_below_json TEXT,
    octave_split_male INTEGER,
    octave_split_female INTEGER,
    section_jump REAL,
    sustained_high_count INTEGER,
    sustained_high_s REAL,
    longest_held_s REAL,
    high_loudness_db REAL,
    syllables_per_s REAL,
    density_source TEXT,
    longest_phrase_s REAL,
    intro_s REAL,
    key INTEGER,
    mode INTEGER,
    key_changes INTEGER,
    energy REAL,
    valence REAL
);
"""

POOL_COLUMNS = (
    "song_key", "pool_id", "artist", "title", "artist_key", "source", "stratum", "spotify_id", "duration_ms",
    "popularity", "lead_gender",
)  # fmt: skip
ANALYSIS_COLUMNS = (
    "song_key", "analysis_version", "low", "high", "p10", "p50", "p90", "voiced_s", "secs_above_json",
    "secs_below_json", "octave_split_male", "octave_split_female", "section_jump", "sustained_high_count",
    "sustained_high_s", "longest_held_s", "high_loudness_db", "syllables_per_s", "density_source",
    "longest_phrase_s", "intro_s", "key", "mode", "key_changes", "energy", "valence",
)  # fmt: skip
# analysis row (BigQuery / queue ``markers``) field -> voice.db column, where they differ
_RENAMED = {"secs_above_json": "secs_above", "secs_below_json": "secs_below"}
_BOOL_COLUMNS = {"octave_split_male", "octave_split_female"}


def _insert(con: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[tuple[Any, ...]]) -> None:
    marks = ",".join("?" * len(columns))
    con.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})", rows)


def _analysis_values(row: dict[str, Any]) -> tuple[Any, ...]:
    out = []
    for col in ANALYSIS_COLUMNS:
        v = row.get(_RENAMED.get(col, col))
        if col in _BOOL_COLUMNS and v is not None:
            v = int(bool(v))
        elif col in _RENAMED and v is not None and not isinstance(v, str):
            v = json.dumps(v, sort_keys=True)
        out.append(v)
    return tuple(out)


def build_voice_db(
    pool_rows: list[dict[str, Any]],
    analysis_rows: list[dict[str, Any]],
    version_stats: dict[str, VersionStats],
    dest: Path,
    run: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write ``dest`` (replaced if present). Returns ``{run, songs, analysed, versions, built_at}``.

    ``pool_rows``: ``POOL_COLUMNS`` + ``versions`` (``Version`` tuple, rank order).
    ``analysis_rows``: ``song_voice_analysis`` rows; those of songs outside the pool are dropped.
    A version's ``embeddable`` is the app's verdict from ``version_stats`` when known, else the
    probe's; ``quality`` is ``versions.version_quality`` of its stats.
    """
    built_at = (now or datetime.now(UTC)).isoformat()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    keys = {p["song_key"] for p in pool_rows}
    analysed = {a["song_key"]: a for a in analysis_rows if a["song_key"] in keys}
    n_versions = 0
    con = sqlite3.connect(dest)
    try:
        con.executescript(DDL)
        meta = {
            "schema": str(SCHEMA_VERSION),
            "run": run,
            "built_at": built_at,
            "analysis_version": str(ANALYSIS_VERSION),
        }
        _insert(con, "meta", ("key", "value"), meta.items())
        _insert(con, "pool", POOL_COLUMNS, (tuple(p.get(c) for c in POOL_COLUMNS) for p in pool_rows))
        versions = []
        for p in pool_rows:
            for v in p.get("versions") or ():
                s = version_stats.get(v.video_id)
                embeddable = s.embeddable if s is not None and s.embeddable is not None else v.embeddable
                emb = None if embeddable is None else int(embeddable)
                versions.append(
                    (p["song_key"], v.video_id, v.brand_code, v.brand_name, v.rank, emb, version_quality(s))
                )
        n_versions = len(versions)
        _insert(
            con,
            "versions",
            ("song_key", "video_id", "brand_code", "brand_name", "rank", "embeddable", "quality"),
            versions,
        )
        _insert(con, "analysis", ANALYSIS_COLUMNS, (_analysis_values(a) for a in analysed.values()))
        con.commit()
    finally:
        con.close()
    return {
        "run": run,
        "songs": len(pool_rows),
        "analysed": len(analysed),
        "versions": n_versions,
        "built_at": built_at,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prune(bucket: Any) -> None:
    runs: dict[str, list[Any]] = {}
    for blob in bucket.list_blobs(prefix=f"{VOICE_PREFIX}/"):
        m = _RUN_DIR.match(blob.name)
        if m:
            runs.setdefault(m.group(1), []).append(blob)
    for run in sorted(runs)[:-KEEP_RUNS]:
        for blob in runs[run]:
            blob.delete()


def publish_voice_db(bucket: Any, path: Path, stats: dict[str, Any], run: str) -> dict[str, Any]:
    """Upload ``voice/<run>/voice.db.gz``, then the pointer (LAST), then prune to ``KEEP_RUNS``."""
    name = f"{VOICE_PREFIX}/{run}/voice.db.gz"
    with bucket.blob(name).open("wb", content_type="application/gzip") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz, path.open("rb") as src:
            shutil.copyfileobj(src, gz, 1 << 20)
    pointer = {
        "run": run,
        "sha256": _sha256(path),
        "songs": stats["songs"],
        "analysed": stats["analysed"],
        "built_at": stats["built_at"],
    }
    with bucket.blob(VOICE_LATEST).open("wb", content_type="application/json") as out:
        out.write(json.dumps(pointer, sort_keys=True).encode())
    try:
        _prune(bucket)
    except Exception:  # noqa: BLE001 - the new voice.db is live; cleanup is retried next publish
        logger.warning("voice.db prune failed after publish (old runs kept)", exc_info=True)
    logger.info("voice.db %s published: %d songs, %d analysed", run, stats["songs"], stats["analysed"])
    return pointer


def _chunks(items: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _catalog_rows(reader: CatalogReader, keys: Sequence[str]) -> tuple[dict[str, Any], dict[str, tuple[Version, ...]]]:
    """(catalog ``songs`` rows, community versions) for ``keys``."""
    conn = reader.conn()
    songs: dict[str, Any] = {}
    version_rows: list[Any] = []
    for chunk in _chunks(keys):
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT song_key, artist_key, popularity, lead_gender FROM songs WHERE song_key IN ({marks})", chunk
        ):
            songs[r["song_key"]] = r
        version_rows.extend(
            conn.execute(
                "SELECT song_key, brand_code, brand_name, youtube_url FROM av.versions"
                f" WHERE kind = 'community' AND youtube_url IS NOT NULL AND song_key IN ({marks})"
                " ORDER BY song_key, rank, rowid",
                chunk,
            )
        )
    return songs, community_versions(version_rows)


def _with_probes(versions: tuple[Version, ...], probed: dict[str, bool]) -> tuple[Version, ...]:
    """The selection's oEmbed verdicts (queue doc ``versions_embeddable``) on the catalog versions."""
    return tuple(replace(v, embeddable=probed[v.video_id]) if v.video_id in probed else v for v in versions)


def _stats(doc: dict[str, Any]) -> VersionStats:
    names = {f.name for f in fields(VersionStats)}
    return VersionStats(**{k: v for k, v in doc.items() if k in names})


async def load_sources(
    fs: Any, reader: CatalogReader
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, VersionStats]]:
    """(pool rows, analysis rows, version stats) for ``build_voice_db``, from the fetch queue,
    the catalog (+ availability.db) and Firestore ``voice_version_stats``."""
    docs = [d for d in await fs.query_documents(FETCH_COLLECTION) if d.get("status") != "failed"]
    docs.sort(key=lambda d: d["song_key"])
    songs, versions = _catalog_rows(reader, [d["song_key"] for d in docs])
    pool: list[dict[str, Any]] = []
    analysis: list[dict[str, Any]] = []
    for d in docs:
        key = d["song_key"]
        cat = songs.get(key)
        pool.append(
            {
                "song_key": key,
                "pool_id": d["id"],
                "artist": d.get("artist"),
                "title": d.get("title"),
                "artist_key": cat["artist_key"] if cat else artist_key(d.get("artist") or ""),
                "source": d.get("source"),
                "stratum": d.get("stratum"),
                "spotify_id": d.get("spotify_id"),
                "duration_ms": d.get("duration_ms"),
                "popularity": cat["popularity"] if cat else None,
                "lead_gender": cat["lead_gender"] if cat else None,
                "versions": _with_probes(versions.get(key, ()), d.get("versions_embeddable") or {}),
            }
        )
        if d.get("status") == "analysed" and d.get("markers"):
            analysis.append({**d["markers"], "song_key": key})
    stats = {d["id"]: _stats(d) for d in await fs.query_documents(VERSION_STATS_COLLECTION)}
    return pool, analysis, stats


async def publish_from_queue(
    fs: Any, bucket: Any, reader: CatalogReader, workdir: Path, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """Build ``workdir/voice.db`` from the current sources and publish it; None (INFO) when the
    pool is still empty. The run id is the build time, UTC ``YYYYMMDD-HHMMSS``."""
    now = now or datetime.now(UTC)
    pool, analysis, stats = await load_sources(fs, reader)
    if not pool:
        logger.info("voice.db: the fetch queue is empty; nothing to publish")
        return None
    run = now.strftime("%Y%m%d-%H%M%S")
    db = workdir / "voice.db"
    built = build_voice_db(pool, analysis, stats, db, run, now=now)
    return publish_voice_db(bucket, db, built, run)
