"""voice-pool-analyse: separated vocals -> markers -> BigQuery rows, pool_measured CSVs and queue
statuses (in-memory Firestore / GCS / BigQuery fakes; synthetic pitch frames, no audio decoding)."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
import requests
from google.api_core.exceptions import (
    DeadlineExceeded,
    Forbidden,
    InternalServerError,
    ServiceUnavailable,
    TooManyRequests,
)

from karaoke_decide.catalog.keys import song_key
from karaoke_decide.etl import vocal_range_refresh as vr
from karaoke_decide.etl.vocal_range_gen import COLUMNS
from karaoke_decide.voice_analysis import analyse
from karaoke_decide.voice_analysis.markers import ANALYSIS_VERSION, FRAME_S, VocalFrames
from karaoke_decide.voice_pool import queue
from karaoke_decide.voice_pool.models import pool_id
from tests.unit.voice_pool.conftest import FakeGcs, MemoryFs

FETCH = queue.FETCH_COLLECTION
GEN = analyse.GEN_BUCKET
DATA = analyse.DATA_BUCKET
US_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "vocal_range" / "ultrastar"
LALAS = ("The Lalas", "Doo Wah Day")  # tests/fixtures/vocal_range/ultrastar/relative.txt (invented)


class FakeBq:
    """Answers the Spotify analysis query per id and records load jobs."""

    def __init__(self, *, fail: Exception | None = None, fail_load: Exception | None = None) -> None:
        self.fail = fail
        self.fail_load = fail_load
        self.batches: list[list[str]] = []
        self.configs: list[Any] = []
        self.loads: list[tuple[str, list[dict[str, Any]], Any]] = []

    def query(self, sql: str, job_config: Any = None) -> Any:
        assert sql == analyse.SPOTIFY_ANALYSIS_SQL
        if self.fail:
            raise self.fail
        ids = next(p.values for p in job_config.query_parameters if p.name == "ids")
        self.batches.append(list(ids))
        self.configs.append(job_config)
        rows = [
            {
                "spotify_id": i,
                "key": 2,
                "mode": 1,
                "key_confidence": 0.9,
                "energy": 0.7,
                "valence": 0.4,
                "duration_ms": 201_000,
                "sections": [
                    {"start": 0.0, "duration": 10.0, "loudness": -20.0, "key": 2, "key_confidence": 0.8},
                    {"start": 10.0, "duration": 10.0, "loudness": -8.0, "key": 2, "key_confidence": 0.8},
                ],
            }
            for i in ids
        ]
        job = MagicMock()
        job.result.side_effect = lambda **_: iter(rows)
        return job

    def load_table_from_json(self, rows: list[dict[str, Any]], table: str, job_config: Any = None) -> Any:
        job = MagicMock()
        if self.fail_load:
            job.result.side_effect = self.fail_load
        else:
            self.loads.append((table, list(rows), job_config))
        return job


def _frames(seconds: float, centre: float = 57.0) -> VocalFrames:
    n = int(round(seconds / FRAME_S))
    t = np.arange(n) * FRAME_S
    return VocalFrames(t=t, midi=centre + 4 * np.sin(t), rms_db=np.full(n, -20.0))


async def _seed(fs: MemoryFs, gcs: FakeGcs, songs: list[tuple[str, str]], *, status: str = "separated",
                with_stem: bool = True) -> list[str]:  # fmt: skip
    pids = []
    for i, (artist, title) in enumerate(songs):
        key = song_key(artist, title)
        pid = pool_id(key)
        await queue.enqueue(fs, [{"song_key": key, "artist": artist, "title": title, "source": "shared",
                                  "spotify_id": f"sp{i:04d}", "duration_ms": 200_000, "stratum": "s"}])  # fmt: skip
        stem = f"voice-pool/stems/{pid}/vocals.flac"
        await fs.update_document(FETCH, pid, {"status": status, "vocals_gcs": f"gs://{GEN}/{stem}"})
        if with_stem:
            gcs.bucket(GEN).objects[stem] = b"fake flac"
        pids.append(pid)
    return pids


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> dict[str, VocalFrames]:
    """Patch audio decoding + pyin: frames per stem object name (default 60 s of voice)."""
    by_stem: dict[str, VocalFrames] = {}
    # load_audio hands vocal_frames the object name in place of samples.
    monkeypatch.setattr(analyse, "load_audio", lambda blob: (blob.name, 44100))
    monkeypatch.setattr(analyse, "vocal_frames", lambda y, sr: by_stem.get(y, _frames(60.0)))
    return by_stem


def _ultrastar_zip(gcs: FakeGcs) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(US_FIXTURES.glob("*.txt")):
            z.writestr(f"usdb/{p.name}", p.read_bytes())
    gcs.bucket(DATA).objects[vr.ZIP_BLOB] = buf.getvalue()


SONGS = [(f"Invented Artist {i}", f"Invented Song {i}") for i in range(7)]


async def test_shard_and_mark_analysed(frames, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    _ultrastar_zip(gcs)
    pids = await _seed(fs, gcs, [*SONGS, LALAS])
    other = await _seed(fs, gcs, [("Invented Artist X", "Not Yet")], status="fetched")

    outs = [await analyse.run_analyse(fs, gcs, bq, run="r1", limit=None, task_index=i, task_count=3) for i in range(3)]
    assert sum(o["analysed"] for o in outs) == len(pids)
    assert all(o["listed"] == o["analysed"] for o in outs)
    docs = fs.cols[FETCH]
    for pid in pids:
        d = docs[pid]
        assert d["status"] == "analysed" and d["analysis_note"] is None and d["error"] is None
        m = d["markers"]
        assert m["analysis_version"] == ANALYSIS_VERSION and m["pool_id"] == pid
        assert 50 < m["p50"] < 64 and m["voiced_s"] == pytest.approx(60.0, abs=0.05)
        assert json.loads(m["secs_above"])["64"] >= 0 and set(json.loads(m["secs_below"])) == {"45", "48", "52"}
        assert (m["energy"], m["valence"], m["duration_ms"], m["key"], m["mode"]) == (0.7, 0.4, 201_000, 2, 1)
    assert docs[other[0]]["status"] == "fetched"

    lalas = docs[pool_id(song_key(*LALAS))]["markers"]
    assert lalas["density_source"] == "ultrastar"
    assert docs[pids[0]]["markers"]["density_source"] == "onsets"

    # Each song analysed exactly once across the shards; one BigQuery row per song.
    rows = [r for table, rs, _ in bq.loads for r in rs if table == analyse.ANALYSIS_TABLE]
    assert sorted(r["pool_id"] for r in rows) == sorted(pids)
    assert set(rows[0]) == set(analyse.ANALYSIS_SCHEMA_FIELDS)
    for _, _, cfg in bq.loads:
        assert cfg.write_disposition == "WRITE_APPEND"
    for cfg in bq.configs:
        assert 0 < cfg.maximum_bytes_billed <= analyse.MAX_BYTES_ANALYSIS
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_spotify_lookup_batches_of_200() -> None:
    bq = FakeBq()
    ids = [f"sp{i:04d}" for i in range(401)] + [None]  # type: ignore[list-item]
    info = analyse.fetch_spotify(bq, ids)
    assert [len(b) for b in bq.batches] == [200, 200, 1]
    assert len(info) == 401
    s = info["sp0001"]
    assert s.track is not None and (s.track.key, s.track.mode) == (2, 1)
    assert [sec.loudness for sec in s.sections or []] == [-20.0, -8.0]


async def test_missing_vocals_marks_failed_with_warning(frames, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    missing, short, silent, ok = await _seed(fs, gcs, SONGS[:4])
    gcs.bucket(GEN).objects.pop(f"voice-pool/stems/{missing}/vocals.flac")
    frames[f"voice-pool/stems/{short}/vocals.flac"] = _frames(10.0)
    frames[f"voice-pool/stems/{silent}/vocals.flac"] = _frames(0.0)

    out = await analyse.run_analyse(fs, gcs, bq, run="r1", limit=None, task_index=0, task_count=1)

    docs = fs.cols[FETCH]
    assert docs[missing]["status"] == "failed" and "vocals" in docs[missing]["error"]
    for pid, note in ((short, "too_little_voice"), (silent, "too_little_voice")):
        assert docs[pid]["status"] == "analysed"  # terminal: the hourly fetch won't restart analyse for it
        assert docs[pid]["markers"] is None and docs[pid]["analysis_note"] == note
    assert docs[ok]["status"] == "analysed" and docs[ok]["markers"] is not None
    assert (out["analysed"], out["too_little_voice"], out["failed"]) == (1, 2, 1)
    rows = [r for _, rs, _ in bq.loads for r in rs]
    assert [r["pool_id"] for r in rows] == [ok]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and missing in warnings[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("too little voice" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)


async def test_csv_rows_for_vocal_range_refresh(frames) -> None:
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    pids = await _seed(fs, gcs, SONGS[:3])
    await analyse.run_analyse(fs, gcs, bq, run="exec-1", limit=None, task_index=0, task_count=1)

    data = gcs.bucket(DATA)
    name = f"{vr.POOL_MEASURED_PREFIX}exec-1/part-0.csv"
    rows = list(csv.DictReader(io.StringIO(data.objects[name].decode())))
    assert list(rows[0]) == COLUMNS
    assert sorted(r["source_id"] for r in rows) == sorted(pids)
    assert {r["lead_part"] for r in rows} == {"all"}

    measured = vr.load_pool_measured(data)
    assert {r.source for r in measured} == {"pool_measured"}
    assert {r.song_key for r in measured} == {song_key(a, t) for a, t in SONGS[:3]}
    assert all(r.weight_s == pytest.approx(60.0, abs=0.05) for r in measured)
    # A re-run of the same task keeps its earlier rows (Cloud Run retry of one execution).
    more = await _seed(fs, gcs, SONGS[3:4])
    await analyse.run_analyse(fs, gcs, bq, run="exec-1", limit=None, task_index=0, task_count=1)
    rows = list(csv.DictReader(io.StringIO(data.objects[name].decode())))
    assert sorted(r["source_id"] for r in rows) == sorted(pids + more)


async def test_limit_and_commit_chunks(frames, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyse, "COMMIT_EVERY", 2)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    await _seed(fs, gcs, SONGS[:6])
    out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=5, task_index=0, task_count=1)
    assert out["analysed"] == 5
    assert [len(rs) for _, rs, _ in bq.loads] == [2, 2, 1]
    assert sum(d["status"] == "separated" for d in fs.cols[FETCH].values()) == 1


async def test_bigquery_forbidden_is_a_warning_and_marks_nothing(frames, caplog) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs = MemoryFs(), FakeGcs()
    pids = await _seed(fs, gcs, SONGS[:2])
    for bq in (FakeBq(fail=Forbidden("no bq")), FakeBq(fail_load=Forbidden("no table"))):
        out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=None, task_index=0, task_count=1)
        assert out["blocked"] and out["analysed"] == 0
        assert {fs.cols[FETCH][p]["status"] for p in pids} == {"separated"}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


async def test_every_song_erroring_is_one_error(frames, monkeypatch, caplog) -> None:
    def boom(blob: Any) -> Any:
        raise RuntimeError("corrupt flac")

    monkeypatch.setattr(analyse, "load_audio", boom)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    pids = await _seed(fs, gcs, SONGS[:3])
    out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=None, task_index=0, task_count=1)
    assert out["failed"] == 3 and out["all_failed"] is True
    assert {fs.cols[FETCH][p]["status"] for p in pids} == {"failed"}
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


async def test_nothing_separated_is_a_quiet_no_op(caplog) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=None, task_index=0, task_count=1)
    assert out["listed"] == 0 and bq.loads == [] and bq.batches == []
    assert gcs.bucket(DATA).objects == {}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_voice_db_analysis_version_matches_markers() -> None:
    from karaoke_decide.voice_pool import voicedb

    assert voicedb.ANALYSIS_VERSION == ANALYSIS_VERSION


def _refuse(monkeypatch: pytest.MonkeyPatch, method: str, name_part: str, exc: Exception, mode: str | None = None):
    """Make FakeBlob.<method> raise ``exc`` for object names containing ``name_part``."""
    from tests.unit.fake_gcs import FakeBlob

    original = getattr(FakeBlob, method)

    def patched(self, *args: Any, **kw: Any) -> Any:
        if name_part in self.name and (mode is None or (args and args[0] == mode)):
            raise exc
        return original(self, *args, **kw)

    monkeypatch.setattr(FakeBlob, method, patched)


@pytest.mark.parametrize(
    ("method", "name_part", "mode"),
    [
        ("open", "pool_measured/", "wb"),  # the CSV write: refused before any BigQuery append
        ("exists", "pool_measured/", None),
        ("exists", "voice-pool/stems/", None),
        ("exists", "ultrastar/", None),
    ],
)
async def test_gcs_forbidden_is_infra_pending(frames, monkeypatch, caplog, method, name_part, mode) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    _ultrastar_zip(gcs)
    pids = await _seed(fs, gcs, SONGS[:3])
    before = {p: dict(fs.cols[FETCH][p]) for p in pids}
    _refuse(monkeypatch, method, name_part, Forbidden("403 no access"), mode)

    out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=None, task_index=0, task_count=1)

    assert out["blocked"] and "403" in out["blocked"] and out["analysed"] == 0
    assert bq.loads == []
    assert {p: fs.cols[FETCH][p] for p in pids} == before
    assert not [n for n in gcs.bucket(DATA).objects if "pool_measured" in n]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.parametrize(
    "exc",
    [
        ServiceUnavailable("503"),
        DeadlineExceeded("slow"),
        TooManyRequests("429"),
        InternalServerError("500"),
        ConnectionError("reset"),
        requests.exceptions.ConnectionError("dns"),
    ],  # fmt: skip
)
async def test_transient_vocals_error_leaves_song_separated(frames, monkeypatch, caplog, exc) -> None:
    caplog.set_level(logging.INFO)
    fs, gcs, bq = MemoryFs(), FakeGcs(), FakeBq()
    flaky, ok = await _seed(fs, gcs, SONGS[:2])
    real = analyse.load_audio

    def load_audio(blob: Any) -> Any:
        if flaky in blob.name:
            raise exc
        return real(blob)

    monkeypatch.setattr(analyse, "load_audio", load_audio)
    out = await analyse.run_analyse(fs, gcs, bq, run="r", limit=None, task_index=0, task_count=1)
    assert (out["analysed"], out["failed"], out["deferred"], out["all_failed"]) == (1, 0, 1, False)
    assert fs.cols[FETCH][flaky]["status"] == "separated" and fs.cols[FETCH][flaky]["error"] is None
    assert fs.cols[FETCH][ok]["status"] == "analysed"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and flaky in warnings[0]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
