"""Tests for the mb-refresh ``vocal_range`` step (karaoke_decide.etl.vocal_range_refresh).

BigQuery is a fake that answers the three lookup queries and captures load jobs, GCS is the
shared in-memory FakeBucket and archive.org is an ``httpx.MockTransport`` serving a tiny zip
built from the hand-written UltraStar fixtures (invented words only, no real lyrics)."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from karaoke_decide.etl import vocal_range_refresh as vr
from karaoke_decide.vocal_range.merge import SourceRow
from tests.unit.fake_gcs import FakeBucket

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "vocal_range" / "ultrastar"
NOW = datetime(2026, 10, 25, 3, 0, 0, tzinfo=UTC)
LALAS_KEY = vr.song_key("The Lalas", "Doo Wah Day")
MEASURED_COLS = "source_id,artist,title,lead_part,low_midi,high_midi,p10,p50,p90,voiced_seconds"


def _zip_bytes() -> bytes:
    """Every fixture .txt, plus one tagged with a YouTube id that KN community knows, plus a non-txt."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path in sorted(FIXTURES.glob("*.txt")):
            z.writestr(f"usdb/{path.name}", path.read_bytes())
        tagged = (FIXTURES / "solo_cp1252.txt").read_bytes().replace(b"#BPM", b"#VIDEO:a=abcdefghijk\n#BPM", 1)
        z.writestr("usdb/tagged_video.txt", tagged)
        z.writestr("usdb/cover.jpg", b"\xff\xd8not really a jpeg")
    return buf.getvalue()


ZIP = _zip_bytes()
ZIP_TXT_FILES = len(list(FIXTURES.glob("*.txt"))) + 1  # + tagged_video.txt
ZIP_PARSED = ZIP_TXT_FILES - 1  # broken_no_bpm.txt has no #BPM


class FakeGCS:
    def __init__(self) -> None:
        self.bucket_obj = FakeBucket()
        self.buckets: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.buckets.append(name)
        return self.bucket_obj


class FakeJob:
    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error

    def result(self, **_: Any) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return self.rows


class FakeBQ:
    def __init__(self, fail_load: bool = False) -> None:
        self.queries: list[tuple[str, Any]] = []
        self.loads: list[tuple[str, list[dict[str, Any]], Any]] = []
        self.fail_load = fail_load
        self.artists = [
            {"mbid": "mbid-lalas", "name": "The Lalas", "gender": "female", "popularity": 50},
            {"mbid": "mbid-low", "name": "The Lalas", "gender": "male", "popularity": 1},  # dup key: less popular
            {"mbid": "mbid-tenor", "name": "Tenor Guy", "gender": "male", "popularity": 10},
        ]

    def query(self, sql: str, job_config: Any = None) -> FakeJob:
        self.queries.append((sql, job_config))
        if "karaokenerds_community" in sql:
            return FakeJob([{"Artist": "KN Artist", "Title": "KN Title", "Watch": "https://youtu.be/abcdefghijk"}])
        if "karaokenerds_raw" in sql and "mb_artists_normalized" not in sql:
            return FakeJob([{"Artist": "The Lalas", "Title": "Doo Wah Day"}])
        if "mb_artists_normalized" in sql:
            return FakeJob(self.artists)
        raise AssertionError(f"unexpected query: {sql[:80]}")

    def load_table_from_json(self, rows: list[dict[str, Any]], dest: str, job_config: Any = None) -> FakeJob:
        if self.fail_load:
            return FakeJob(error=RuntimeError("load exploded"))
        self.loads.append((dest, list(rows), job_config))
        return FakeJob()


class Origin:
    """archive.org stand-in: serves ZIP at the item's download URL and counts requests."""

    def __init__(self, status: int = 200) -> None:
        self.calls = 0
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert str(request.url) == vr.USDB_ZIP_URL
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, content=ZIP, headers={"content-length": str(len(ZIP))})


@pytest.fixture(autouse=True)
def _zip_checksum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vr, "USDB_ZIP_SHA1", hashlib.sha1(ZIP).hexdigest())  # noqa: S324 - archive.org publishes sha1
    monkeypatch.setattr(vr, "USDB_ZIP_BYTES", len(ZIP))


def _write_gz_csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(header + "\n" + "".join(r + "\n" for r in rows))


def _data_dir(tmp_path: Path, *, measured: list[str] | None = None, gemini: bool = False) -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True)
    (d / "wikidata_p412.csv").write_text("mbid,vtLabel\nmbid-tenor,tenor\nmbid-lalas,soprano\n", encoding="utf-8")
    if measured is not None:
        _write_gz_csv(d / "nomad_measured.csv.gz", MEASURED_COLS, measured)
    if gemini:
        header = "kind,key,artist,title,pop_rank,low_midi,high_midi,p10,p50,p90,voice_type,confidence"
        _write_gz_csv(
            d / "gemini_songs.csv.gz",
            header,
            ["song,song:1,Gem Artist,Gem Song,10,50,70,52,60,68,tenor,0.8"],
        )
        _write_gz_csv(
            d / "gemini_artists.csv.gz",
            header,
            ["artist,artist:mbid-tenor,Tenor Guy,,5,48,69,50,58,66,tenor,0.9"],
        )
    return d


def _step(tmp_path: Path, bq: FakeBQ | None = None, gcs: FakeGCS | None = None, origin: Origin | None = None, **kw):
    bq = bq or FakeBQ()
    gcs = gcs or FakeGCS()
    origin = origin or Origin()
    data_dir = kw.pop("data_dir", None) or _data_dir(tmp_path, **kw)
    with httpx.Client(transport=httpx.MockTransport(origin)) as http:
        summary = vr.run_step(bq, gcs, http, now=NOW, data_dir=data_dir)
    return summary, bq, gcs, origin


def _tsv(blob: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(gzip.decompress(blob).decode("utf-8")), delimiter="\t"))


# --------------------------------------------------------------------------


def test_zip_fetched_once_then_reused(tmp_path: Path) -> None:
    gcs, origin = FakeGCS(), Origin()
    _step(tmp_path / "a", gcs=gcs, origin=origin)
    assert origin.calls == 1
    assert gcs.bucket_obj.objects[vr.ZIP_BLOB] == ZIP
    assert gcs.bucket_obj.metadata[vr.ZIP_BLOB]["sha1"] == vr.USDB_ZIP_SHA1
    summary, *_ = _step(tmp_path / "b", gcs=gcs, origin=origin)
    assert origin.calls == 1  # second run reads the GCS copy, never archive.org
    assert summary["ultrastar"]["files"] == ZIP_TXT_FILES


def test_zip_checksum_mismatch_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vr, "USDB_ZIP_SHA1", "0" * 40)
    gcs = FakeGCS()
    with pytest.raises(vr.RefreshError, match="SHA1"):
        _step(tmp_path, gcs=gcs)
    assert vr.ZIP_BLOB not in gcs.bucket_obj.objects


def test_tables_written_and_pointer_last(tmp_path: Path) -> None:
    summary, bq, gcs, _ = _step(tmp_path, measured=[], gemini=True)
    bucket = gcs.bucket_obj
    assert set(gcs.buckets) == {"nomadkaraoke-musicbrainz-data"}

    dests = [d for d, _, _ in bq.loads]
    assert dests == [vr.SOURCES_TABLE, vr.SONGS_TABLE, vr.ARTISTS_TABLE]
    for _, _, cfg in bq.loads:
        assert cfg.write_disposition == "WRITE_TRUNCATE"
        assert cfg.source_format == "NEWLINE_DELIMITED_JSON"
    schemas = {d: [f.name for f in cfg.schema] for d, _, cfg in bq.loads}
    assert "loaded_at" in schemas[vr.SOURCES_TABLE] and "confidence" in schemas[vr.SOURCES_TABLE]
    assert {"crowd_comfortable", "crowd_too_low", "crowd_too_high", "confidence"} <= set(schemas[vr.SONGS_TABLE])
    assert {"artist_key", "basis", "confidence"} <= set(schemas[vr.ARTISTS_TABLE])
    songs = next(rows for d, rows, _ in bq.loads if d == vr.SONGS_TABLE)
    assert all(r["crowd_comfortable"] is None and r["crowd_too_low"] is None for r in songs)
    sources = next(rows for d, rows, _ in bq.loads if d == vr.SOURCES_TABLE)
    gem = [r for r in sources if r["source"] == "gemini"]
    assert gem and gem[0]["confidence"] == 0.8 and gem[0]["octave_confidence"] is None and gem[0]["pop_rank"] == 10
    assert all(r["loaded_at"] == NOW.isoformat() for r in sources)

    # Every query is capped.
    assert bq.queries and all(cfg.maximum_bytes_billed for _, cfg in bq.queries)

    # GCS: TSVs + summary, pointer last and consistent with the uploaded bytes.
    assert bucket.writes[-1] == vr.POINTER_BLOB
    assert set(bucket.writes[-4:-1]) == {vr.SONG_TSV_BLOB, vr.ARTIST_TSV_BLOB, vr.SUMMARY_BLOB}
    pointer = json.loads(bucket.objects[vr.POINTER_BLOB])
    assert pointer == {
        "run": "20261025-030000",
        "sha256_song": hashlib.sha256(bucket.objects[vr.SONG_TSV_BLOB]).hexdigest(),
        "sha256_artist": hashlib.sha256(bucket.objects[vr.ARTIST_TSV_BLOB]).hexdigest(),
    }
    song_rows = _tsv(bucket.objects[vr.SONG_TSV_BLOB])
    assert {r["song_key"] for r in song_rows} == {r["song_key"] for r in songs}
    lalas = next(r for r in song_rows if r["song_key"] == LALAS_KEY)
    assert lalas["tier"] == "notes" and lalas["source"] == "ultrastar"
    artist_rows = _tsv(bucket.objects[vr.ARTIST_TSV_BLOB])
    assert {r["artist_key"]: r["basis"] for r in artist_rows}["tenor guy"] in {"gemini", "p412"}
    assert json.loads(bucket.objects[vr.SUMMARY_BLOB]) == summary

    # Summary shape.
    for key in (
        "rows_per_source",
        "songs_per_tier",
        "artists_per_basis",
        "ultrastar",
        "gemini_song_bands",
        "gemini_artist_bands",
        "unmatched_per_source",
        "join_paths",
    ):
        assert key in summary, key
    us = summary["ultrastar"]
    assert (us["files"], us["parsed"], us["failed"]) == (ZIP_TXT_FILES, ZIP_PARSED, 1)
    assert summary["join_paths"]["ultrastar_video_id"] == 1
    assert summary["join_paths"]["ultrastar_song_key"] == ZIP_PARSED - 1
    assert summary["rows_per_source"]["gemini"] == 1
    band = summary["gemini_song_bands"][0]
    assert {"band", "n", "median_err", "own_median_err", "kept", "decision"} <= set(band)
    assert "pooled" in summary["gemini_song_calibration"]


def test_video_id_join_uses_community_song_key(tmp_path: Path) -> None:
    _, bq, _, _ = _step(tmp_path)
    sources = next(rows for d, rows, _ in bq.loads if d == vr.SOURCES_TABLE)
    tagged = next(r for r in sources if r["source_id"].endswith("tagged_video.txt"))
    assert tagged["song_key"] == vr.song_key("KN Artist", "KN Title")


def test_ultrastar_rows_are_octave_repaired_with_lead_gender(tmp_path: Path) -> None:
    _, bq, _, _ = _step(tmp_path)
    sources = next(rows for d, rows, _ in bq.loads if d == vr.SOURCES_TABLE)
    solo = next(r for r in sources if r["source_id"].endswith("solo_utf8.txt"))
    # The Lalas are female (most popular ARTISTS_SQL row): p50 64 already sits in E3-E5.
    assert solo["p50"] == 64.0 and solo["octave_confidence"] == 0.9
    assert solo["weight_s"] == pytest.approx(26 * 60 / (120 * 4))  # 26 sung beats at BPM 120
    duet = [r for r in sources if r["source_id"].endswith("duet_p1_p2.txt")]
    assert sorted(r["lead_part"] for r in duet) == ["P1", "P2"]


def test_summary_has_octave_error_rate(tmp_path: Path) -> None:
    measured = [
        "nomad1,The Lalas,Doo Wah Day,all,48,55,49,52,55,60",  # an octave below the notes' p50 (64)
        "nomad2,Unknown Band,Not In KN,all,50,60,52,55,58,",  # resumable short-stem row: no low_midi
        "nomad3,Unknown Band,Not In KN 2,all,,,,,,5",
    ]
    summary, bq, _, _ = _step(tmp_path, measured=measured)
    us = summary["ultrastar"]
    assert us["octave_pairs"] == 1
    assert us["octave_error_rate"] == 1.0
    assert summary["rows_per_source"]["nomad_measured"] == 2  # nomad3 (empty low_midi) dropped
    songs = next(rows for d, rows, _ in bq.loads if d == vr.SONGS_TABLE)
    lalas = next(r for r in songs if r["song_key"] == LALAS_KEY)
    assert (lalas["tier"], lalas["source"], lalas["n_sources"]) == ("measured", "nomad_measured", 2)
    assert summary["unmatched_per_source"]["nomad_measured"] == 1


def test_step_failure_keeps_previous_pointer_and_logs_one_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    gcs = FakeGCS()
    old = b'{"run": "20261018-030000", "sha256_artist": "a", "sha256_song": "s"}'
    gcs.bucket_obj.objects[vr.POINTER_BLOB] = old
    gcs.bucket_obj.objects[vr.SONG_TSV_BLOB] = b"old song tsv"
    bq = FakeBQ(fail_load=True)
    data_dir = _data_dir(tmp_path)
    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(Origin())) as http:
        assert vr.run_step_safe(bq, gcs, http, now=NOW, data_dir=data_dir) is None
    assert gcs.bucket_obj.objects[vr.POINTER_BLOB] == old
    assert gcs.bucket_obj.objects[vr.SONG_TSV_BLOB] == b"old song tsv"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "vocal_range" in errors[0].getMessage()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_origin_failure_logs_one_error_and_writes_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    gcs, bq = FakeGCS(), FakeBQ()
    data_dir = _data_dir(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(Origin(status=404))) as http:
        assert vr.run_step_safe(bq, gcs, http, now=NOW, data_dir=data_dir) is None
    assert gcs.bucket_obj.objects == {} and bq.loads == []
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_missing_gen_measured_is_info(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="vocal_range_refresh")
    summary, *_ = _step(tmp_path)  # no gen_measured CSVs, no nomad CSV, no Gemini files
    assert summary["rows_per_source"].get("gen_measured", 0) == 0
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("gen_measured" in m for m in info)
    assert any("nomad_measured" in m for m in info)
    assert any("gemini" in m.lower() for m in info)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    # Parse failures are DEBUG per file, never louder.
    assert any(r.levelno == logging.DEBUG and "broken_no_bpm" in r.getMessage() for r in caplog.records)


def test_gen_measured_csvs_are_read_from_gcs(tmp_path: Path) -> None:
    gcs = FakeGCS()
    b = gcs.bucket_obj
    b.objects["vocal-range/gen_measured/run1/part-0.csv"] = (
        MEASURED_COLS + "\njob1,The Lalas,Doo Wah Day,all,50,62,52,57,60,40\njob2,X,Y,all,,,,,,3\n"
    ).encode()
    b.objects["vocal-range/gen_measured/run1/notes.txt"] = b"ignored"
    b.objects["vocal-range/gen_measured/run1/sub/part-1.csv"] = b"ignored too (nested)"
    summary, bq, _, _ = _step(tmp_path, gcs=gcs)
    assert summary["rows_per_source"]["gen_measured"] == 1
    songs = next(rows for d, rows, _ in bq.loads if d == vr.SONGS_TABLE)
    assert next(r for r in songs if r["song_key"] == LALAS_KEY)["source"] == "gen_measured"


def test_gemini_kept_rejects_rows_outside_bands() -> None:
    from karaoke_decide.vocal_range.calibrate import BandStats

    kept = vr.band_predicate([BandStats("1-5k", 0, None, True), BandStats("5k-20k", 0, None, False)], "song")

    def row(rank: int | None) -> SourceRow:
        return SourceRow("k", "gemini", "id", "a", "t", 1, 2, 1, 1, 2, None, "all", 0.0, rank, 0.9)

    assert kept(row(10)) is True
    assert kept(row(6000)) is False  # band not kept
    assert kept(row(None)) is False
    assert kept(row(60_000)) is False  # outside every band


def test_usdb_delta_absence_is_silent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="vocal_range_refresh")
    _step(tmp_path)
    assert not [r for r in caplog.records if vr.USDB_DELTA_FILE in r.getMessage()]


def test_calibrated_gemini_band_survives_merge() -> None:
    """30 Gemini estimates within 1 semitone of measured truth keep band 1-5k, so a Gemini-only
    song in that band is published as tier 'estimated' with its Gemini confidence."""

    def row(source: str, i: int, p50: float, rank: int | None = None, conf: float | None = None) -> SourceRow:
        return SourceRow(
            f"a{i}|s{i}", source, f"{source}{i}", f"A{i}", f"S{i}", p50 - 6, p50 + 6, p50 - 4, p50, p50 + 4,
            None, "all", 60.0 if source != "gemini" else 0.0, rank, conf,
        )  # fmt: skip

    measured = [row("gen_measured", i, 57) for i in range(30)]
    gemini = [row("gemini", i, 58, rank=i + 1, conf=0.7) for i in range(30)]
    gemini.append(row("gemini", 99, 60, rank=100, conf=0.6))  # no truth: Gemini-only
    gemini.append(row("gemini", 98, 60, rank=None, conf=0.9))  # unranked: never kept
    result = vr.compute(
        ultrastar=vr.UltraStarResult(rows=[]),
        measured=measured,
        gemini_songs=gemini,
        gemini_artists=[],
        p412={},
        mbid_of={},
        gender_of={},
        kn_keys=set(),
    )
    band = result.summary["gemini_song_bands"][0]
    assert (band["band"], band["n"], band["kept"], band["decision"]) == ("1-5k", 30, True, "own")
    by_key = {s.song_key: s for s in result.songs}
    assert (by_key["a99|s99"].tier, by_key["a99|s99"].confidence) == ("estimated", 0.6)
    assert "a98|s98" not in by_key
    assert by_key["a0|s0"].tier == "measured"
