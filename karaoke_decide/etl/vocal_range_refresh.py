"""mb-refresh ``vocal_range`` step: every vocal-range source -> song/artist ranges in BQ + GCS.

Runs inside the weekly ``mb-refresh`` job, immediately before the catalog build (so
catalog.db always sees fresh tables), or alone via
``python -m karaoke_decide.etl.musicbrainz_refresh vocal-range``.

Inputs (optional ones missing -> one INFO line each, never louder):

* UltraStar notes: archive.org item ``usdb_20250204`` (``usdb.zip``, 77,975,058 bytes, SHA1
  verified), fetched ONCE into ``gs://nomadkaraoke-musicbrainz-data/vocal-range/ultrastar/``
  and reused from there. Every ``.txt`` is parsed from memory; lyrics never touch disk and
  never leave this process (only derived pitch statistics are kept). Each part is
  octave-repaired against the artist's lead gender; parse failures are counted (DEBUG per file).
* Measured CSVs (pyin): ``data/vocal_range/nomad_measured.csv.gz`` (NomadPC script) and
  ``gs://.../vocal-range/gen_measured/<run>/part-*.csv`` (vocal-range-gen job). Rows with an
  empty ``low_midi`` (short stems, written so the scripts can resume) are dropped.
* Gemini estimates: ``data/vocal_range/gemini_{songs,artists}.csv.gz``, calibrated per
  popularity band against the measured/notes truth; only kept bands survive the merge.
* Wikidata P412 voice types (``data/vocal_range/wikidata_p412.csv``) and artist mbid/gender
  from 3a's ``bq_source.ARTISTS_SQL`` (keyed by ``keys.artist_key``).
* KaraokeNerds song keys (``karaokenerds_raw`` + ``karaokenerds_community``) for the
  match/unmatched counts and the UltraStar ``video_id`` join (spec §5.2).

Outputs, in this order (``latest.json`` LAST, so a failure leaves last week's pointer):

1. BigQuery load jobs, WRITE_TRUNCATE: ``song_vocal_range_sources``, ``song_vocal_range``,
   ``artist_vocal_range`` in ``karaoke_decide``.
2. ``vocal-range/latest/{song,artist}_vocal_range.tsv.gz`` + ``latest/summary.json``.
3. ``vocal-range/latest.json`` = ``{run, sha256_song, sha256_artist}`` (sha256 of the .tsv.gz bytes).

Everything is computed in memory before the first write. A failure anywhere logs ONE ERROR
(``run_step_safe``) and never fails the MB refresh.
"""

from __future__ import annotations

import csv
import dataclasses
import gzip
import hashlib
import io
import json
import logging
import re
import statistics
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from google.cloud import bigquery

from karaoke_decide.catalog.bq_source import ARTISTS_SQL
from karaoke_decide.catalog.bq_source import P as PROD_DATASET
from karaoke_decide.catalog.keys import artist_key, song_key
from karaoke_decide.etl.refresh_common import RefreshError, resumable_chunks
from karaoke_decide.services.bq_limits import GIB, MAX_BYTES_CATALOG
from karaoke_decide.vocal_range import calibrate
from karaoke_decide.vocal_range.calibrate import ARTIST_BANDS, SONG_BANDS, BandStats
from karaoke_decide.vocal_range.merge import ArtistRange, MergedSong, SourceRow, artist_ranges, merge_songs
from karaoke_decide.vocal_range.octave import octave_error_rate, repair_octave, shifted
from karaoke_decide.vocal_range.p412 import load_p412
from karaoke_decide.vocal_range.ultrastar import note_stats, parse_ultrastar

__all__ = ["RefreshError", "run_step", "run_step_safe", "song_key"]

logger = logging.getLogger("vocal_range_refresh")

BUCKET = "nomadkaraoke-musicbrainz-data"
PREFIX = "vocal-range"
ZIP_BLOB = f"{PREFIX}/ultrastar/usdb_20250204.zip"
GEN_MEASURED_PREFIX = f"{PREFIX}/gen_measured/"
SONG_TSV_BLOB = f"{PREFIX}/latest/song_vocal_range.tsv.gz"
ARTIST_TSV_BLOB = f"{PREFIX}/latest/artist_vocal_range.tsv.gz"
SUMMARY_BLOB = f"{PREFIX}/latest/summary.json"
POINTER_BLOB = f"{PREFIX}/latest.json"

# archive.org item usdb_20250204 (metadata API, checked 2026-10-22): usdb.zip, sha1 + size below.
USDB_ZIP_URL = "https://archive.org/download/usdb_20250204/usdb.zip"
USDB_ZIP_SHA1 = "bcd14ba46eaf8f5d42d8cc2a26c46dc96056f8bd"
USDB_ZIP_BYTES = 77_975_058

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "vocal_range"
NOMAD_MEASURED_FILE = "nomad_measured.csv.gz"
GEMINI_SONGS_FILE = "gemini_songs.csv.gz"
GEMINI_ARTISTS_FILE = "gemini_artists.csv.gz"
P412_FILE = "wikidata_p412.csv"
# Future: USDB songs added since 2025-02, as derived stats only. Not produced yet; when it
# appears, add a loader here. Its absence is expected and deliberately not logged.
USDB_DELTA_FILE = "usdb_delta_stats.csv.gz"

SOURCES_TABLE = f"{PROD_DATASET}.song_vocal_range_sources"
SONGS_TABLE = f"{PROD_DATASET}.song_vocal_range"
ARTISTS_TABLE = f"{PROD_DATASET}.artist_vocal_range"

# ARTISTS_SQL is the catalog's own query (same text + config, so the catalog build that follows
# hits the BigQuery cache); the KN lookups read two small tables.
MAX_BYTES_ARTISTS = MAX_BYTES_CATALOG
MAX_BYTES_KN = 5 * GIB

KN_RAW_SQL = f"""
SELECT Artist, Title FROM `{PROD_DATASET}.karaokenerds_raw`
WHERE Artist IS NOT NULL AND Title IS NOT NULL
"""
KN_COMMUNITY_SQL = f"""
SELECT Artist, Title, Watch FROM `{PROD_DATASET}.karaokenerds_community`
WHERE Artist IS NOT NULL AND Title IS NOT NULL
"""

_YT_ID_RE = re.compile(r"(?:youtu\.be/|[?&]v=|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})")
_GEN_PART_RE = re.compile(rf"^{re.escape(GEN_MEASURED_PREFIX)}[^/]+/part-[^/]*\.csv$")
_FLOAT = "FLOAT64"


def _schema(*fields: tuple[str, str]) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(name, kind, mode="NULLABLE") for name, kind in fields]


_RANGE = (("low_midi", _FLOAT), ("high_midi", _FLOAT), ("p10", _FLOAT), ("p50", _FLOAT), ("p90", _FLOAT))
SOURCES_SCHEMA = _schema(
    ("song_key", "STRING"),
    ("source", "STRING"),
    ("source_id", "STRING"),
    ("artist", "STRING"),
    ("title", "STRING"),
    *_RANGE,
    ("octave_confidence", _FLOAT),
    ("lead_part", "STRING"),
    ("weight_s", _FLOAT),
    ("pop_rank", "INT64"),
    ("confidence", _FLOAT),
    ("loaded_at", "TIMESTAMP"),
)
CROWD_FIELDS = ("crowd_comfortable", "crowd_too_low", "crowd_too_high")  # Phase 4, always NULL for now
SONGS_SCHEMA = _schema(
    ("song_key", "STRING"),
    *_RANGE,
    ("tier", "STRING"),
    ("source", "STRING"),
    ("octave_confidence", _FLOAT),
    ("is_duet", "BOOL"),
    ("n_sources", "INT64"),
    ("confidence", _FLOAT),
    *((name, "INT64") for name in CROWD_FIELDS),
)
ARTISTS_SCHEMA = _schema(("artist_key", "STRING"), *_RANGE, ("basis", "STRING"), ("confidence", _FLOAT))


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def _query(bq: Any, sql: str, max_bytes: int) -> Iterable[dict[str, Any]]:
    job = bq.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes, use_query_cache=True))
    return (dict(r) for r in job.result())


def load_artists(bq: Any) -> tuple[dict[str, str], dict[str, str]]:
    """(mbid_of, gender_of) keyed by artist_key; the most popular row wins a shared key."""
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    for r in _query(bq, ARTISTS_SQL, MAX_BYTES_ARTISTS):
        key = artist_key(r.get("name"))
        if not key:
            continue
        pop = int(r.get("popularity") or -1)
        if key not in best or pop > best[key][0]:
            best[key] = (pop, r)
    mbid_of = {k: str(r["mbid"]) for k, (_, r) in best.items() if r.get("mbid")}
    gender_of = {k: str(r["gender"]) for k, (_, r) in best.items() if r.get("gender")}
    return mbid_of, gender_of


def youtube_id(url: str | None) -> str | None:
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def load_kn(bq: Any) -> tuple[set[str], dict[str, str]]:
    """(KN song keys, YouTube id -> song_key) from karaokenerds_raw + karaokenerds_community."""
    keys = {song_key(r["Artist"], r["Title"]) for r in _query(bq, KN_RAW_SQL, MAX_BYTES_KN)}
    by_video: dict[str, str] = {}
    for r in _query(bq, KN_COMMUNITY_SQL, MAX_BYTES_KN):
        key = song_key(r["Artist"], r["Title"])
        keys.add(key)
        if vid := youtube_id(r.get("Watch")):
            by_video.setdefault(vid, key)
    return keys, by_video


def fetch_ultrastar_zip(bucket: Any, http: httpx.Client) -> bytes:
    """The USDB zip from our GCS copy; on first use, download it from archive.org (SHA1-checked)."""
    blob = bucket.blob(ZIP_BLOB)
    if blob.exists():
        blob.reload()
        if (blob.metadata or {}).get("sha1") == USDB_ZIP_SHA1:
            logger.info(f"Reusing gs://{BUCKET}/{ZIP_BLOB} (no download)")
            with blob.open("rb") as f:
                data: bytes = f.read()
            return data
    logger.info(f"Downloading {USDB_ZIP_URL} -> gs://{BUCKET}/{ZIP_BLOB}")
    # Buffer in memory (78 MB) and verify BEFORE writing, so a bad download never lands in GCS.
    data = b"".join(resumable_chunks(http, USDB_ZIP_URL))
    actual = hashlib.sha1(data).hexdigest()  # noqa: S324 - archive.org publishes sha1/md5 only
    if actual != USDB_ZIP_SHA1 or len(data) != USDB_ZIP_BYTES:
        raise RefreshError(
            f"SHA1/size mismatch for {USDB_ZIP_URL}: got {actual} ({len(data)} bytes), "
            f"expected {USDB_ZIP_SHA1} ({USDB_ZIP_BYTES} bytes)"
        )
    with blob.open("wb", content_type="application/zip") as out:
        out.write(data)
    blob.metadata = {"sha1": USDB_ZIP_SHA1, "source_url": USDB_ZIP_URL}
    blob.patch()
    return data


@dataclasses.dataclass
class UltraStarResult:
    rows: list[SourceRow]
    files: int = 0
    parsed: int = 0
    failed: int = 0
    by_video_id: int = 0
    by_song_key: int = 0
    song_keys: set[str] = dataclasses.field(default_factory=set)


def ultrastar_rows(zip_bytes: bytes, gender_of: dict[str, str], by_video: dict[str, str]) -> UltraStarResult:
    """One octave-repaired SourceRow per part of every parseable .txt in the zip."""
    out = UltraStarResult(rows=[])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".txt"):
                continue
            out.files += 1
            song = parse_ultrastar(z.read(info))
            if song is None:
                out.failed += 1
                logger.debug(f"UltraStar parse failed: {info.filename}")
                continue
            out.parsed += 1
            if song.video_id and song.video_id in by_video:
                key = by_video[song.video_id]
                out.by_video_id += 1
            else:
                key = song_key(song.artist, song.title)
                out.by_song_key += 1
            out.song_keys.add(key)
            gender = gender_of.get(artist_key(song.artist))
            for part, notes in sorted(song.parts.items()):
                stats = note_stats(notes)
                if stats is None:
                    continue
                shift, conf = repair_octave(stats, gender)
                s = shifted(stats, shift)
                out.rows.append(
                    SourceRow(
                        song_key=key,
                        source="ultrastar",
                        source_id=info.filename,
                        artist=song.artist,
                        title=song.title,
                        low_midi=s.low,
                        high_midi=s.high,
                        p10=s.p10,
                        p50=s.p50,
                        p90=s.p90,
                        octave_confidence=conf,
                        lead_part=part,
                        weight_s=s.sung_beats * 60 / (song.bpm * 4),
                    )
                )
    return out


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def measured_rows(lines: Iterable[str], source: str) -> Iterator[SourceRow]:
    """Measured CSV (source_id,artist,title,lead_part,low_midi,...,voiced_seconds) -> SourceRows."""
    for r in csv.DictReader(lines):
        nums = [_float(r.get(c)) for c in ("low_midi", "high_midi", "p10", "p50", "p90")]
        if any(n is None for n in nums):  # empty low_midi = short stem, kept only for resuming
            continue
        lo, hi, p10, p50, p90 = (float(n) for n in nums if n is not None)
        yield SourceRow(
            song_key=song_key(r.get("artist"), r.get("title")),
            source=source,  # type: ignore[arg-type]
            source_id=r.get("source_id") or "",
            artist=r.get("artist") or "",
            title=r.get("title") or "",
            low_midi=lo,
            high_midi=hi,
            p10=p10,
            p50=p50,
            p90=p90,
            octave_confidence=None,
            lead_part=r.get("lead_part") or "all",
            weight_s=_float(r.get("voiced_seconds")) or 0.0,
        )


def load_nomad_measured(data_dir: Path) -> list[SourceRow]:
    path = data_dir / NOMAD_MEASURED_FILE
    if not path.exists():
        logger.info(f"vocal_range: no nomad_measured input ({path} missing); skipping that source")
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(measured_rows(f, "nomad_measured"))


def load_gen_measured(bucket: Any) -> list[SourceRow]:
    blobs = [b for b in bucket.list_blobs(prefix=GEN_MEASURED_PREFIX) if _GEN_PART_RE.match(b.name)]
    if not blobs:
        logger.info(f"vocal_range: no gen_measured input (gs://{BUCKET}/{GEN_MEASURED_PREFIX}*/part-*.csv); skipping")
        return []
    rows: list[SourceRow] = []
    for blob in blobs:
        with blob.open("rb") as f:
            text = f.read().decode("utf-8")
        rows.extend(measured_rows(io.StringIO(text, newline=""), "gen_measured"))
    return rows


def _gemini_rows(path: Path, kind: str) -> list[SourceRow]:
    if not path.exists():
        logger.info(f"vocal_range: no Gemini {kind} estimates ({path} missing); skipping")
        return []
    rows: list[SourceRow] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            nums = [_float(r.get(c)) for c in ("low_midi", "high_midi", "p10", "p50", "p90")]
            if any(n is None for n in nums):
                continue
            lo, hi, p10, p50, p90 = (float(n) for n in nums if n is not None)
            rank = _float(r.get("pop_rank"))
            key = song_key(r.get("artist"), r.get("title")) if kind == "song" else artist_key(r.get("artist"))
            rows.append(
                SourceRow(
                    song_key=key,
                    source="gemini",
                    source_id=r.get("key") or "",
                    artist=r.get("artist") or "",
                    title=r.get("title") or "",
                    low_midi=lo,
                    high_midi=hi,
                    p10=p10,
                    p50=p50,
                    p90=p90,
                    octave_confidence=None,  # Gemini confidence lives in .confidence, never here
                    lead_part="all",
                    weight_s=0.0,
                    pop_rank=int(rank) if rank is not None else None,
                    confidence=_float(r.get("confidence")),
                )
            )
    return rows


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


def band_predicate(bands: list[BandStats], kind: str) -> Callable[[SourceRow], bool]:
    """True iff the row's pop_rank falls in a calibrated band that was kept."""
    spec = SONG_BANDS if kind == "song" else ARTIST_BANDS
    kept = {b.band for b in bands if b.kept}

    def ok(r: SourceRow) -> bool:
        if r.pop_rank is None:
            return False
        band = next((name for name, lo, hi in spec if lo <= r.pop_rank <= hi), None)
        return band is not None and band in kept

    return ok


def _song_pooled(est: list[SourceRow], truth: dict[str, MergedSong]) -> dict[str, Any]:
    """The pooled figures behind calibrate_songs' decision for bands with n < MIN_N."""
    errs = []
    for r in est:
        t = truth.get(r.song_key)
        in_band = r.pop_rank is not None and any(lo <= r.pop_rank <= hi for _, lo, hi in SONG_BANDS)
        is_truth = t is not None and (
            t.tier == "measured"
            or (
                t.tier == "notes"
                and t.octave_confidence is not None
                and t.octave_confidence >= calibrate.MIN_TRUTH_CONFIDENCE
            )
        )
        if in_band and is_truth and t is not None:
            errs.append((abs(r.p10 - t.p10) + abs(r.p50 - t.p50) + abs(r.p90 - t.p90)) / 3)
    med = float(statistics.median(errs)) if errs else None
    kept = len(errs) >= calibrate.MIN_N and med is not None and med <= calibrate.MAX_MEDIAN_ERR
    return {"n": len(errs), "median_err": med, "kept": kept}


def _band_dicts(bands: list[BandStats], poolable: bool) -> list[dict[str, Any]]:
    return [
        {
            **dataclasses.asdict(b),
            "own_median_err": b.median_err,
            "decision": "pooled" if poolable and b.n < calibrate.MIN_N else "own",
        }
        for b in bands
    ]


@dataclasses.dataclass
class Result:
    sources: list[SourceRow]
    songs: list[MergedSong]
    artists: list[ArtistRange]
    summary: dict[str, Any]


def compute(
    *,
    ultrastar: UltraStarResult,
    measured: list[SourceRow],
    gemini_songs: list[SourceRow],
    gemini_artists: list[SourceRow],
    p412: dict[str, set[str]],
    mbid_of: dict[str, str],
    gender_of: dict[str, str],
    kn_keys: set[str],
) -> Result:
    """Pure: merge, octave-error check, Gemini calibration, artist ranges and the summary."""
    never: Callable[[SourceRow], bool] = lambda _r: False  # noqa: E731
    truth_rows = [*measured, *ultrastar.rows]
    truth_songs = merge_songs(truth_rows, never)
    truth = {s.song_key: s for s in truth_songs}

    # Octave-error rate: repaired UltraStar p50 vs measured p50 where both exist.
    us_best = {s.song_key: s for s in merge_songs(ultrastar.rows, never)}
    meas_best = {s.song_key: s for s in merge_songs(measured, never)}
    pairs = [(us_best[k].p50, meas_best[k].p50) for k in sorted(us_best.keys() & meas_best.keys())]
    err_rate, n_pairs = octave_error_rate(pairs)

    song_bands = calibrate.calibrate_songs(gemini_songs, truth)
    gemini_kept = band_predicate(song_bands, "song")
    sources = [*truth_rows, *gemini_songs]
    songs = merge_songs(sources, gemini_kept)

    artist_of: dict[str, str] = {}
    for r in sources:
        artist_of.setdefault(r.song_key, artist_key(r.artist))
    songs_basis = {
        a.artist_key: a for a in artist_ranges(truth_songs, artist_of, [], never, {}, {}, {}) if a.basis == "songs"
    }
    p412_by_artist = {k: p412[m] for k, m in mbid_of.items() if m in p412}
    artist_bands = calibrate.calibrate_artists(gemini_artists, p412_by_artist, songs_basis)
    artists = artist_ranges(
        songs, artist_of, gemini_artists, band_predicate(artist_bands, "artist"), p412, mbid_of, gender_of
    )

    rows_per_source = Counter(r.source for r in sources)
    unmatched = Counter(r.source for r in sources if r.song_key not in kn_keys)
    summary: dict[str, Any] = {
        "rows_per_source": dict(sorted(rows_per_source.items())),
        "gemini_artist_rows": len(gemini_artists),
        "songs_per_tier": dict(sorted(Counter(s.tier for s in songs).items())),
        "songs_per_source": dict(sorted(Counter(s.source for s in songs).items())),
        "duets": sum(1 for s in songs if s.is_duet),
        "artists_per_basis": dict(sorted(Counter(a.basis for a in artists).items())),
        "ultrastar": {
            "files": ultrastar.files,
            "parsed": ultrastar.parsed,
            "failed": ultrastar.failed,
            "rows": len(ultrastar.rows),
            "matched": len(ultrastar.song_keys & kn_keys),
            "octave_error_rate": err_rate,
            "octave_pairs": n_pairs,
        },
        "gemini_song_bands": _band_dicts(song_bands, poolable=True),
        "gemini_song_calibration": {"pooled": _song_pooled(gemini_songs, truth)},
        "gemini_artist_bands": _band_dicts(artist_bands, poolable=False),
        "unmatched_per_source": {s: unmatched.get(s, 0) for s in sorted(rows_per_source)},
        "join_paths": {
            "ultrastar_video_id": ultrastar.by_video_id,
            "ultrastar_song_key": ultrastar.by_song_key,
            "measured_song_key": len(measured),
            "gemini_song_key": len(gemini_songs),
        },
    }
    return Result(sources, songs, artists, summary)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


def _load(bq: Any, table: str, rows: list[dict[str, Any]], schema: list[bigquery.SchemaField]) -> None:
    config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    bq.load_table_from_json(rows, table, job_config=config).result()
    logger.info(f"Loaded {table}: {len(rows):,} rows")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def tsv_gz(rows: list[Any], columns: list[str]) -> bytes:
    """Header + one line per dataclass row; NULL = empty, bool = 1/0; gzip mtime 0 (stable sha)."""
    text = io.StringIO()
    w = csv.writer(text, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    for r in rows:
        d = dataclasses.asdict(r)
        w.writerow([_cell(d[c]) for c in columns])
    return gzip.compress(text.getvalue().encode("utf-8"), mtime=0)


def _put(bucket: Any, name: str, data: bytes, content_type: str) -> None:
    with bucket.blob(name).open("wb", content_type=content_type) as out:
        out.write(data)


def publish(bq: Any, bucket: Any, result: Result, run: str, now: datetime) -> None:
    loaded_at = now.isoformat()
    _load(
        bq, SOURCES_TABLE, [{**dataclasses.asdict(r), "loaded_at": loaded_at} for r in result.sources], SOURCES_SCHEMA
    )
    crowd = dict.fromkeys(CROWD_FIELDS)
    _load(bq, SONGS_TABLE, [{**dataclasses.asdict(s), **crowd} for s in result.songs], SONGS_SCHEMA)
    _load(bq, ARTISTS_TABLE, [dataclasses.asdict(a) for a in result.artists], ARTISTS_SCHEMA)

    song_tsv = tsv_gz(result.songs, [f.name for f in dataclasses.fields(MergedSong)])
    artist_tsv = tsv_gz(result.artists, [f.name for f in dataclasses.fields(ArtistRange)])
    _put(bucket, SONG_TSV_BLOB, song_tsv, "application/gzip")
    _put(bucket, ARTIST_TSV_BLOB, artist_tsv, "application/gzip")
    _put(bucket, SUMMARY_BLOB, json.dumps(result.summary, sort_keys=True, indent=1).encode(), "application/json")
    pointer = {
        "run": run,
        "sha256_song": hashlib.sha256(song_tsv).hexdigest(),
        "sha256_artist": hashlib.sha256(artist_tsv).hexdigest(),
    }
    _put(bucket, POINTER_BLOB, json.dumps(pointer, sort_keys=True).encode(), "application/json")  # LAST


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def run_step(
    bq: Any, gcs: Any, http: httpx.Client, *, now: datetime | None = None, data_dir: Path = DATA_DIR
) -> dict[str, Any]:
    """Build and publish the vocal-range tables; returns the summary. Raises on failure."""
    now = now or datetime.now(UTC)
    run = now.strftime("%Y%m%d-%H%M%S")
    bucket = gcs.bucket(BUCKET)

    mbid_of, gender_of = load_artists(bq)
    kn_keys, by_video = load_kn(bq)
    ultrastar = ultrastar_rows(fetch_ultrastar_zip(bucket, http), gender_of, by_video)
    measured = [*load_gen_measured(bucket), *load_nomad_measured(data_dir)]
    p412_path = data_dir / P412_FILE
    p412 = load_p412(p412_path) if p412_path.exists() else {}
    if not p412:
        logger.info(f"vocal_range: no Wikidata P412 voice types ({p412_path}); artist p412 basis unavailable")
    result = compute(
        ultrastar=ultrastar,
        measured=measured,
        gemini_songs=_gemini_rows(data_dir / GEMINI_SONGS_FILE, "song"),
        gemini_artists=_gemini_rows(data_dir / GEMINI_ARTISTS_FILE, "artist"),
        p412=p412,
        mbid_of=mbid_of,
        gender_of=gender_of,
        kn_keys=kn_keys,
    )
    result.summary["run"] = run
    publish(bq, bucket, result, run, now)
    logger.info(f"vocal_range published run {run}: {json.dumps(result.summary, sort_keys=True)}")
    return result.summary


def run_step_safe(
    bq: Any, gcs: Any, http: httpx.Client | None = None, *, now: datetime | None = None, data_dir: Path = DATA_DIR
) -> dict[str, Any] | None:
    """``run_step`` that never raises: ONE ERROR on failure; previous tables + latest.json kept."""
    try:
        if http is not None:
            return run_step(bq, gcs, http, now=now, data_dir=data_dir)
        with httpx.Client(headers={"User-Agent": "nomadkaraoke-mb-refresh/1.0 (https://nomadkaraoke.com)"}) as client:
            return run_step(bq, gcs, client, now=now, data_dir=data_dir)
    except Exception:  # noqa: BLE001 - best-effort step, must not fail the MB refresh
        logger.exception("vocal_range step failed; previous vocal-range tables and latest.json left as-is")
        return None
