"""voice-pool-analyse: separated vocals -> per-song markers -> BigQuery + 3c ranges.

For the fetch-queue songs at ``separated`` in this task's shard (``sha1(pool_id) % task_count``,
``CLOUD_RUN_TASK_INDEX`` / ``CLOUD_RUN_TASK_COUNT``), oldest first, up to ``limit``:

1. download ``vocals_gcs`` (gen bucket) -> 3c's pyin frames (``markers.vocal_frames``);
2. Spotify sections, track key/mode and energy/valence/duration from BigQuery: ONE query per
   batch of <= 200 Spotify ids, byte-capped (``MAX_BYTES_ANALYSIS``);
3. an UltraStar file matched by ``song_key(artist, title)`` from 3c's USDB zip (syllable density);
4. ``compute_markers``.

Every ``COMMIT_EVERY`` songs (and at the end) the results are committed in this order:
BigQuery ``karaoke_decide.song_voice_analysis`` (WRITE_APPEND; readers take the latest row per
``(song_key, analysis_version)``), this task's 3c measured CSV
``gs://nomadkaraoke-musicbrainz-data/vocal-range/pool_measured/<run>/part-<task>.csv`` (read by
the weekly vocal-range refresh as ``pool_measured``), then the queue docs. A crash between the
steps only re-analyses a song (a duplicate BigQuery row; the latest wins).

Queue outcomes, all terminal so the hourly fetch never restarts this job for the same song:

* ``analysed`` with ``markers`` = its BigQuery row (voice.db is built from these);
* ``analysed`` with ``markers: None`` and ``analysis_note: "too_little_voice"`` when the stem has
  under 3c's ``MIN_VOICED_S`` (20 s) of voice (INFO; no BigQuery row or CSV line);
* ``failed`` when the vocals object is missing (WARNING) or decoding/analysis raises (WARNING;
  ONE ERROR, from task 0 only, when every song of the run failed that way).

BigQuery missing or forbidden (infra not applied yet) is a WARNING: the run stops, nothing is
marked, and the next hourly hand-off retries. voice.db is published by the CLI afterwards.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import Forbidden, NotFound
from google.cloud import bigquery

from karaoke_decide.catalog.bq_source import P
from karaoke_decide.catalog.keys import song_key
from karaoke_decide.etl.vocal_range_gen import _csv_bytes, _fmt, load_audio, shard
from karaoke_decide.etl.vocal_range_refresh import BUCKET as DATA_BUCKET
from karaoke_decide.etl.vocal_range_refresh import POOL_MEASURED_PREFIX, ZIP_BLOB
from karaoke_decide.services.bq_limits import MAX_BYTES_BATCH
from karaoke_decide.vocal_range.pitch import MIN_VOICED_S
from karaoke_decide.vocal_range.ultrastar import UltraStarSong, parse_ultrastar
from karaoke_decide.voice_analysis.markers import (
    ANALYSIS_VERSION,
    FRAME_S,
    Section,
    SongMarkers,
    TrackAnalysis,
    compute_markers,
    vocal_frames,
)
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION
from karaoke_decide.voice_pool.separate import GEN_BUCKET

__all__ = [
    "ANALYSIS_TABLE",
    "DATA_BUCKET",
    "GEN_BUCKET",
    "analysis_row",
    "fetch_spotify",
    "run_analyse",
]

logger = logging.getLogger(__name__)

ANALYSIS_TABLE = f"{P}.song_voice_analysis"
SPOTIFY_BATCH = 200
COMMIT_EVERY = 25
# Pre-pruning estimate: the three Spotify tables' scanned columns (~35 GiB unclustered); the
# same class as the batch jobs that full-scan spotify_tracks + spotify_audio_features.
MAX_BYTES_ANALYSIS = MAX_BYTES_BATCH
TOO_LITTLE_VOICE = "too_little_voice"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INFRA_MISSING = (NotFound, Forbidden)

SPOTIFY_ANALYSIS_SQL = f"""
WITH sec AS (
  SELECT spotify_id,
         ARRAY_AGG(STRUCT(start, duration, loudness, key, key_confidence) ORDER BY section_index) AS sections
  FROM `{P}.spotify_audio_analysis_sections`
  WHERE spotify_id IN UNNEST(@ids)
  GROUP BY spotify_id
), trk AS (
  SELECT spotify_id, key, mode, key_confidence
  FROM `{P}.spotify_audio_analysis_tracks`
  WHERE spotify_id IN UNNEST(@ids)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY spotify_id ORDER BY key_confidence DESC) = 1
), af AS (
  SELECT track_id AS spotify_id, energy, valence, duration_ms
  FROM `{P}.spotify_audio_features`
  WHERE track_id IN UNNEST(@ids)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY track_id ORDER BY duration_ms DESC) = 1
)
SELECT id AS spotify_id, trk.key, trk.mode, trk.key_confidence,
       af.energy, af.valence, af.duration_ms, sec.sections
FROM UNNEST(@ids) AS id
LEFT JOIN trk ON trk.spotify_id = id
LEFT JOIN af ON af.spotify_id = id
LEFT JOIN sec ON sec.spotify_id = id
"""

_F, _I, _S, _B = "FLOAT64", "INT64", "STRING", "BOOL"
ANALYSIS_SCHEMA = [
    bigquery.SchemaField(name, kind, mode="NULLABLE")
    for name, kind in (
        ("song_key", _S),
        ("pool_id", _S),
        ("analysis_version", _I),
        ("analysed_at", "TIMESTAMP"),
        ("low", _F),
        ("high", _F),
        ("p10", _F),
        ("p50", _F),
        ("p90", _F),
        ("voiced_s", _F),
        ("secs_above", _S),  # JSON {"<midi>": seconds}
        ("secs_below", _S),
        ("octave_split_male", _B),
        ("octave_split_female", _B),
        ("section_jump", _F),
        ("sustained_high_count", _I),
        ("sustained_high_s", _F),
        ("longest_held_s", _F),
        ("high_loudness_db", _F),
        ("syllables_per_s", _F),
        ("density_source", _S),
        ("longest_phrase_s", _F),
        ("intro_s", _F),
        ("key", _I),
        ("mode", _I),
        ("key_changes", _I),
        ("energy", _F),
        ("valence", _F),
        ("duration_ms", _I),
    )
]
ANALYSIS_SCHEMA_FIELDS = tuple(f.name for f in ANALYSIS_SCHEMA)


class InfraUnavailableError(Exception):
    """BigQuery table/dataset missing or forbidden: infra not applied yet."""


@dataclass
class SpotifyInfo:
    sections: list[Section] | None
    track: TrackAnalysis | None
    energy: float | None
    valence: float | None
    duration_ms: int | None


def _float(v: Any) -> float | None:
    return None if v is None else float(v)


def _info(r: dict[str, Any]) -> SpotifyInfo:
    sections = [
        Section(
            float(s["start"]), float(s["duration"]), float(s["loudness"]), int(s["key"]), float(s["key_confidence"])
        )
        for s in r.get("sections") or []
        if None not in (s.get("start"), s.get("duration"), s.get("loudness"), s.get("key"), s.get("key_confidence"))
    ]
    track = None
    if r.get("key") is not None and r.get("mode") is not None:
        track = TrackAnalysis(int(r["key"]), int(r["mode"]), float(r.get("key_confidence") or 0.0))
    dur = r.get("duration_ms")
    return SpotifyInfo(
        sections or None, track, _float(r.get("energy")), _float(r.get("valence")), None if dur is None else int(dur)
    )


def fetch_spotify(bq: Any, spotify_ids: Iterable[str | None]) -> dict[str, SpotifyInfo]:
    """``{spotify_id: SpotifyInfo}``: one byte-capped query per <= 200 ids. A missing or
    forbidden table raises ``InfraUnavailableError``."""
    ids = sorted({i for i in spotify_ids if i})
    out: dict[str, SpotifyInfo] = {}
    for lo in range(0, len(ids), SPOTIFY_BATCH):
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids[lo : lo + SPOTIFY_BATCH])],
            maximum_bytes_billed=MAX_BYTES_ANALYSIS,
            use_query_cache=True,
        )
        try:
            rows = bq.query(SPOTIFY_ANALYSIS_SQL, job_config=config).result()
        except _INFRA_MISSING as e:
            raise InfraUnavailableError(f"Spotify analysis query: {e}") from e
        for r in rows:
            out[r["spotify_id"]] = _info(dict(r))
    return out


def load_ultrastar(bucket: Any, wanted: set[str]) -> dict[str, UltraStarSong]:
    """UltraStar songs from 3c's USDB zip whose ``song_key(artist, title)`` is in ``wanted`` (first
    file by name wins). No zip yet -> INFO and none."""
    if not wanted:
        return {}
    blob = bucket.blob(ZIP_BLOB)
    if not blob.exists():
        logger.info("voice analysis: no UltraStar zip at gs://%s/%s; densities come from onsets", DATA_BUCKET, ZIP_BLOB)
        return {}
    with blob.open("rb") as f:
        data = f.read()
    out: dict[str, UltraStarSong] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.is_dir() or not info.filename.lower().endswith(".txt"):
                continue
            song = parse_ultrastar(z.read(info))  # lyrics stay in memory, never logged or stored
            if song is None:
                continue
            key = song_key(song.artist, song.title)
            if key in wanted and key not in out:
                out[key] = song
    return out


def analysis_row(
    doc: dict[str, Any], m: SongMarkers, info: SpotifyInfo | None, analysed_at: datetime
) -> dict[str, Any]:
    """The ``song_voice_analysis`` row (also stored as the queue doc's ``markers``)."""
    d = asdict(m)
    for name in ("secs_above", "secs_below"):
        d[name] = json.dumps({str(k): round(v, 3) for k, v in sorted(d[name].items())})
    return {
        "song_key": doc["song_key"],
        "pool_id": doc["id"],
        "analysis_version": ANALYSIS_VERSION,
        "analysed_at": analysed_at.isoformat(),
        **d,
        "energy": info.energy if info else None,
        "valence": info.valence if info else None,
        "duration_ms": (info.duration_ms if info and info.duration_ms else None) or doc.get("duration_ms"),
    }


def _csv_row(doc: dict[str, Any], m: SongMarkers) -> dict[str, str]:
    return {
        "source_id": doc["id"],
        "artist": doc.get("artist") or "",
        "title": doc.get("title") or "",
        "lead_part": "all",
        "low_midi": _fmt(m.low),
        "high_midi": _fmt(m.high),
        "p10": _fmt(m.p10),
        "p50": _fmt(m.p50),
        "p90": _fmt(m.p90),
        "voiced_seconds": _fmt(m.voiced_s),
    }


def _vocals_blob(gcs: Any, uri: str | None) -> Any | None:
    if not uri or not uri.startswith("gs://") or "/" not in uri[5:]:
        return None
    bucket, name = uri[5:].split("/", 1)
    blob = gcs.bucket(bucket).blob(name)
    return blob if blob.exists() else None


class _Shard:
    """One task's pending outcomes plus its CSV part file (rewritten in full on each commit)."""

    def __init__(self, fs: Any, gcs: Any, bq: Any, run: str, task_index: int) -> None:
        self.fs, self.bq = fs, bq
        self.csv_blob = gcs.bucket(DATA_BUCKET).blob(f"{POOL_MEASURED_PREFIX}{run}/part-{task_index}.csv")
        self.csv_rows: list[dict[str, str]] = []
        if self.csv_blob.exists():  # a retried task keeps the rows it already wrote
            with self.csv_blob.open("rb") as f:
                self.csv_rows = list(csv.DictReader(io.StringIO(f.read().decode())))
        self.bq_rows: list[dict[str, Any]] = []
        self.new_csv = 0
        self.updates: list[tuple[str, str, dict[str, Any]]] = []  # (pool_id, outcome, update)
        self.done: Counter[str] = Counter()  # committed outcomes

    def pending(self) -> int:
        return len(self.updates)

    async def commit(self) -> None:
        if self.bq_rows:
            config = bigquery.LoadJobConfig(
                schema=ANALYSIS_SCHEMA,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            )
            try:
                self.bq.load_table_from_json(self.bq_rows, ANALYSIS_TABLE, job_config=config).result()
            except _INFRA_MISSING as e:
                raise InfraUnavailableError(f"load into {ANALYSIS_TABLE}: {e}") from e
        if self.new_csv:
            with self.csv_blob.open("wb", content_type="text/csv") as f:
                f.write(_csv_bytes(self.csv_rows))
        for pid, outcome, update in self.updates:
            await self.fs.update_document(FETCH_COLLECTION, pid, update)
            self.done[outcome] += 1
        self.bq_rows, self.updates, self.new_csv = [], [], 0


async def run_analyse(
    fs: Any, gcs: Any, bq: Any, *, run: str, limit: int | None, task_index: int, task_count: int
) -> dict[str, Any]:
    """Analyse this task's share of ``separated`` songs (see module doc). Returns
    ``{run, task_index, task_count, listed, analysed, too_little_voice, failed, blocked}``."""
    docs = await fs.query_documents(FETCH_COLLECTION, filters=[("status", "==", "separated")])
    mine = set(shard([d["id"] for d in docs], task_index, task_count))
    todo = sorted((d for d in docs if d["id"] in mine), key=lambda d: (d.get("updated_at") or _EPOCH, d["id"]))
    if limit is not None:
        todo = todo[:limit]
    out: dict[str, Any] = {"run": run, "task_index": task_index, "task_count": task_count, "listed": len(todo)}
    if not todo:
        logger.info("voice analysis %s task %d/%d: nothing separated in this shard", run, task_index, task_count)
        return {**out, "analysed": 0, "too_little_voice": 0, "failed": 0, "blocked": None}

    data = gcs.bucket(DATA_BUCKET)
    work = _Shard(fs, gcs, bq, run, task_index)
    try:
        spotify = fetch_spotify(bq, (d.get("spotify_id") for d in todo))
        ultrastar = load_ultrastar(data, {d["song_key"] for d in todo})
        for doc in todo:
            await _analyse_one(doc, gcs, spotify, ultrastar, work)
            if work.pending() >= COMMIT_EVERY:
                await work.commit()
        await work.commit()
        blocked = None
    except InfraUnavailableError as e:
        logger.warning(
            "voice analysis %s: %s; infra pending? Uncommitted songs stay separated for the next run", run, e
        )
        blocked = str(e)
    done = work.done
    summary = {
        **out,
        "analysed": done["analysed"],
        "too_little_voice": done[TOO_LITTLE_VOICE],
        "failed": done["missing"] + done["error"],
        "blocked": blocked,
    }
    if done["error"] and done["error"] == len(todo):
        # A real failure (bad image, broken decoder): one ERROR per execution, from task 0.
        log = logger.error if task_index == 0 else logger.warning
        log("voice analysis %s: every song failed (%d); see the WARNINGs above", run, done["error"])
    logger.info("voice analysis done: %s", summary)
    return summary


async def _analyse_one(
    doc: dict[str, Any],
    gcs: Any,
    spotify: dict[str, SpotifyInfo],
    ultrastar: dict[str, UltraStarSong],
    work: _Shard,
) -> None:
    pid = doc["id"]
    now = datetime.now(UTC)
    blob = _vocals_blob(gcs, doc.get("vocals_gcs"))
    if blob is None:
        logger.warning("voice analysis: %s (%s) has no vocals at %s", pid, doc.get("song_key"), doc.get("vocals_gcs"))
        work.updates.append((pid, "missing", {"status": "failed", "error": "vocals stem missing", "updated_at": now}))
        return
    try:
        y, sr = load_audio(blob)
        frames = vocal_frames(y, sr)
        voiced_s = len(frames.t) * FRAME_S
        markers = None
        if voiced_s >= MIN_VOICED_S:
            info = spotify.get(doc.get("spotify_id") or "")
            try:
                markers = compute_markers(
                    frames,
                    sections=info.sections if info else None,
                    track=info.track if info else None,
                    ultrastar=ultrastar.get(doc["song_key"]),
                )
            except ValueError:  # no voiced frames (unreachable past the 20 s check, kept as a guard)
                markers = None
    except Exception as e:  # noqa: BLE001 - one bad stem must not stop the shard
        logger.warning("voice analysis: %s (%s) failed: %s: %s", pid, doc.get("song_key"), type(e).__name__, e)
        work.updates.append(
            (pid, "error", {"status": "failed", "error": f"analysis: {type(e).__name__}: {e}", "updated_at": now})
        )
        return
    base = {"status": "analysed", "analysis_version": ANALYSIS_VERSION, "analysed_at": now, "updated_at": now}
    if markers is None:
        logger.info(
            "voice analysis: %s (%s) has too little voice (%.1f s); no markers", pid, doc.get("song_key"), voiced_s
        )
        work.updates.append(
            (pid, TOO_LITTLE_VOICE, {**base, "markers": None, "analysis_note": TOO_LITTLE_VOICE, "error": None})
        )
        return
    row = analysis_row(doc, markers, spotify.get(doc.get("spotify_id") or ""), now)
    work.bq_rows.append(row)
    work.csv_rows.append(_csv_row(doc, markers))
    work.new_csv += 1
    work.updates.append((pid, "analysed", {**base, "markers": row, "analysis_note": None, "error": None}))
