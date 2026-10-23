"""VoiceDataStore: loads voice.db (local dir or GCS pointer), hot-refreshes it, never raises."""

from __future__ import annotations

import asyncio
import dataclasses
import gzip
import hashlib
import io
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.config import BackendSettings, get_backend_settings
from backend.services import catalog_store, voice_data_store
from backend.services.voice_data_store import (
    PoolSong,
    SongAnalysis,
    VoiceDataStore,
    VoiceDataUnavailable,
    get_voice_data_store,
)
from karaoke_decide.voice_pool import fixtures
from karaoke_decide.voice_pool.models import Version
from karaoke_decide.voice_pool.voicedb import ANALYSIS_COLUMNS, build_voice_db

BUCKET = "nomadkaraoke-musicbrainz-data"
RUN_A = "20261022-010101"
RUN_B = "20261023-010101"


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket = bucket
        self.name = name

    def _data(self) -> bytes:
        self.bucket.reads.append(self.name)
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)  # stands in for google.api_core NotFound
        return self.bucket.objects[self.name]

    def download_as_bytes(self) -> bytes:
        return self._data()

    def open(self, mode: str = "rb", **kwargs: Any) -> io.BytesIO:
        assert mode == "rb"
        return io.BytesIO(self._data())


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.reads: list[str] = []

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def publish(self, db: Path, run: str, *, sha: str | None = None) -> None:
        raw = db.read_bytes()
        self.objects[f"voice/{run}/voice.db.gz"] = gzip.compress(raw)
        self.objects["voice/latest.json"] = json.dumps(
            {
                "run": run,
                "sha256": sha or hashlib.sha256(raw).hexdigest(),
                "songs": 48,
                "analysed": 48,
                "built_at": "2026-10-22T00:00:00+00:00",
            }
        ).encode()


class FakeClient:
    def __init__(self, bucket: FakeBucket | None = None) -> None:
        self.buckets = {BUCKET: bucket or FakeBucket()}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.fixture
def new_store() -> Iterator[Callable[[], VoiceDataStore]]:
    created: list[VoiceDataStore] = []

    def make() -> VoiceDataStore:
        created.append(VoiceDataStore())
        return created[-1]

    yield make
    for store in created:
        store.close()


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fixtures.build(tmp_path_factory.mktemp("voice-fixture"))


def _settings(tmp_path: Path, **kw: Any) -> BackendSettings:
    base: dict[str, Any] = {"catalog_dir": str(tmp_path / "dl"), "voice_refresh_s": 600}
    base.update(kw)
    return BackendSettings(**base)


def _small_db(path: Path, run: str) -> Path:
    """Two analysed songs + one (too_little_voice) without analysis; versions out of rank order."""
    pool = [
        {
            "song_key": key,
            "pool_id": key[:4],
            "artist": artist,
            "title": title,
            "artist_key": artist.lower(),
            "source": "shared",
            "stratum": "gender=male|register=low",
            "spotify_id": None,
            "duration_ms": 200_000,
            "popularity": 70,
            "lead_gender": "male",
            "versions": versions,
        }
        for key, artist, title, versions in (
            (
                "alpha|one",
                "Alpha",
                "One",
                (Version("vidB", "BB", "Brand B", 2, None), Version("vidA", "AA", "Brand A", 1, True)),
            ),
            ("beta|two", "Beta", "Two", (Version("vidC", "CC", "Brand C", 1, False),)),
            ("gamma|quiet", "Gamma", "Quiet", ()),
        )
    ]
    analysis = [
        {"song_key": k, "analysis_version": 1, "low": 45.0, "high": 64.0, "p50": 55.0}
        for k in ("alpha|one", "beta|two")
    ]
    build_voice_db(pool, analysis, {}, path, run)
    return path


# ---------------------------------------------------------------- loading


def test_startup_without_voice_db_is_warming(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = new_store()
    with caplog.at_level(logging.WARNING):
        store.load(_settings(tmp_path), client=FakeClient())
    assert store.status == "warming_up" and store.run is None
    with pytest.raises(VoiceDataUnavailable):
        store.require()
    assert not _errors(caplog)
    assert any(r.levelno == logging.WARNING and "voice" in r.getMessage().lower() for r in caplog.records)


def test_missing_local_dir_and_no_client_are_warnings(
    new_store: Callable[[], VoiceDataStore],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = new_store()
    with caplog.at_level(logging.WARNING):
        store.load(_settings(tmp_path, voice_local_dir=str(tmp_path / "nothing")))
    assert store.status == "warming_up"

    def no_client() -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(catalog_store, "_storage_client", no_client)
    with caplog.at_level(logging.WARNING):
        store.load(_settings(tmp_path))
    assert store.status == "warming_up"
    assert not _errors(caplog)


def test_local_dir_loads_fixture(new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path) -> None:
    store = new_store()
    store.load(_settings(tmp_path, voice_local_dir=str(fixture_db.parent)), client=FakeClient())
    assert store.status == "ready" and store.run == "fixture"
    reader = store.require()
    keys = reader.pool_keys()
    assert len(keys) == fixtures.PER_TYPE * 6 and fixtures.STARLIGHT in keys
    song = reader.song(fixtures.STARLIGHT)
    assert song is not None
    assert song == PoolSong(
        song_key=fixtures.STARLIGHT,
        artist="Muse",
        title="Starlight",
        artist_key=song.artist_key,
        stratum="fixture|baritone",
        source="shared",
        duration_ms=song.duration_ms,
        popularity=song.popularity,
        lead_gender=song.lead_gender,
    )
    a = reader.analysis(fixtures.STARLIGHT)
    assert isinstance(a, SongAnalysis) and a.song_key == fixtures.STARLIGHT
    assert a.low is not None and a.p50 is not None and a.high is not None and a.low < a.p50 < a.high
    assert isinstance(a.octave_split_male, bool)
    assert [v.video_id for v in reader.versions(fixtures.STARLIGHT)] == ["FIXTURE1"]
    assert reader.versions(fixtures.STARLIGHT)[0].embeddable is True
    assert [s.song_key for s in reader.pool_songs(keys[:3])] == keys[:3]
    assert reader.song("nobody|nothing") is None and reader.analysis("nobody|nothing") is None


def test_song_analysis_matches_voicedb_columns() -> None:
    assert tuple(f.name for f in dataclasses.fields(SongAnalysis)) == ANALYSIS_COLUMNS


def test_pool_only_offers_analysed_songs_and_versions_by_rank(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path
) -> None:
    _small_db(tmp_path / "local" / "voice.db", "small")
    store = new_store()
    store.load(_settings(tmp_path, voice_local_dir=str(tmp_path / "local")))
    reader = store.require()
    assert reader.pool_keys() == ["alpha|one", "beta|two"]
    assert [s.song_key for s in reader.pool_songs(["gamma|quiet", "beta|two", "alpha|one"])] == [
        "beta|two",
        "alpha|one",
    ]
    assert reader.song("gamma|quiet") is not None  # in pool, no analysis
    assert reader.analysis("gamma|quiet") is None
    assert reader.versions("alpha|one") == [
        Version("vidA", "AA", "Brand A", 1, True),
        Version("vidB", "BB", "Brand B", 2, None),
    ]
    assert reader.versions("beta|two") == [Version("vidC", "CC", "Brand C", 1, False)]
    assert reader.versions("gamma|quiet") == []


def test_gcs_load_downloads_and_verifies(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path
) -> None:
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A)
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket))
    assert store.status == "ready" and store.run == RUN_A
    assert (tmp_path / "dl" / "voice" / RUN_A / "voice.db").is_file()
    assert store.require().song(fixtures.STARLIGHT) is not None


def test_corrupt_first_download_stays_warming(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A, sha="0" * 64)
    store = new_store()
    with caplog.at_level(logging.WARNING):
        store.load(_settings(tmp_path), client=FakeClient(bucket))
    assert store.status == "warming_up"
    assert not _errors(caplog)
    assert not list((tmp_path / "dl" / "voice" / RUN_A).glob("*.part"))


def test_malformed_pointer_stays_warming(new_store: Callable[[], VoiceDataStore], tmp_path: Path) -> None:
    bucket = FakeBucket()
    bucket.objects["voice/latest.json"] = b"{not json"
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket))
    assert store.status == "warming_up"
    bucket.objects["voice/latest.json"] = json.dumps({"run": RUN_A}).encode()
    store.load(_settings(tmp_path), client=FakeClient(bucket))
    assert store.status == "warming_up"


# ---------------------------------------------------------------- refresh


def test_refresh_swaps_on_new_run(new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path) -> None:
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A)
    store = new_store()
    store.load(_settings(tmp_path, voice_refresh_s=600), client=FakeClient(bucket), now=1000.0)
    first = store.require()

    bucket.publish(_small_db(tmp_path / "b" / "voice.db", RUN_B), RUN_B)
    bucket.reads.clear()
    assert store.maybe_refresh(1500.0) is False  # within voice_refresh_s: not even the pointer is read
    assert bucket.reads == [] and store.run == RUN_A

    assert store.maybe_refresh(1700.0) is True
    assert store.run == RUN_B
    second = store.require()
    assert second is not first and second.pool_keys() == ["alpha|one", "beta|two"]
    assert first.song(fixtures.STARLIGHT) is not None  # in-flight readers of the old run keep working

    bucket.reads.clear()
    assert store.maybe_refresh(2400.0) is False  # same run: pointer read, nothing downloaded
    assert bucket.reads == ["voice/latest.json"]


def test_corrupt_sha_keeps_previous(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A)
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket), now=0.0)
    before = store.require()
    bucket.publish(_small_db(tmp_path / "b" / "voice.db", RUN_B), RUN_B, sha="f" * 64)
    with caplog.at_level(logging.WARNING):
        assert store.maybe_refresh(10_000.0) is False
    assert store.run == RUN_A and store.require() is before
    assert not _errors(caplog)
    assert any("voice.db" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_refresh_recovers_from_warming_up(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path
) -> None:
    bucket = FakeBucket()
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket), now=0.0)
    assert store.status == "warming_up"
    bucket.publish(fixture_db, RUN_A)
    assert store.maybe_refresh(601.0) is True
    assert store.status == "ready" and store.run == RUN_A


def test_refresh_with_local_dir_loads_when_it_appears(new_store: Callable[[], VoiceDataStore], tmp_path: Path) -> None:
    local = tmp_path / "local"
    store = new_store()
    store.load(_settings(tmp_path, voice_local_dir=str(local)), now=0.0)
    assert store.status == "warming_up"
    _small_db(local / "voice.db", "small")
    assert store.maybe_refresh(601.0) is True
    assert store.run == "small"
    assert store.maybe_refresh(1300.0) is False  # local voice.db is not re-read once loaded


def test_maybe_refresh_before_load_is_a_noop(new_store: Callable[[], VoiceDataStore]) -> None:
    assert new_store().maybe_refresh(10_000.0) is False


def test_old_downloads_are_removed_two_swaps_later(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path
) -> None:
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A)
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket), now=0.0)
    bucket.publish(_small_db(tmp_path / "b" / "voice.db", RUN_B), RUN_B)
    assert store.maybe_refresh(1000.0)
    run_c = "20261024-010101"
    bucket.publish(_small_db(tmp_path / "c" / "voice.db", run_c), run_c)
    assert store.maybe_refresh(2000.0)
    assert not (tmp_path / "dl" / "voice" / RUN_A).exists()  # retired twice: deleted
    assert (tmp_path / "dl" / "voice" / RUN_B / "voice.db").is_file()  # last retired: kept for in-flight reads
    assert store.run == run_c


async def test_refresh_loop_calls_maybe_refresh(
    new_store: Callable[[], VoiceDataStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = new_store()
    calls: list[float] = []

    def record(now: float) -> bool:
        calls.append(now)
        return False

    monkeypatch.setattr(store, "maybe_refresh", record)
    monkeypatch.setattr(voice_data_store, "REFRESH_TICK_S", 0.01)
    task = asyncio.create_task(store.refresh_forever())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


async def test_refresh_loop_survives_errors(
    new_store: Callable[[], VoiceDataStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = new_store()
    calls: list[float] = []

    def boom(now: float) -> bool:
        calls.append(now)
        raise RuntimeError("unexpected")

    monkeypatch.setattr(store, "maybe_refresh", boom)
    monkeypatch.setattr(voice_data_store, "REFRESH_TICK_S", 0.01)
    task = asyncio.create_task(store.refresh_forever())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


def test_get_voice_data_store_is_singleton() -> None:
    assert get_voice_data_store() is get_voice_data_store()


def test_require_dependency_answers_503_warming_up() -> None:
    from backend.api.routes.admin_voice import get_voice_reader

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_voice_reader(VoiceDataStore()))
    assert exc.value.status_code == 503 and exc.value.detail == "warming_up"


# ---------------------------------------------------------------- lifespan


@pytest.fixture
def fresh_singletons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr(catalog_store, "_store", None)
    monkeypatch.setattr(voice_data_store, "_store", None)
    monkeypatch.setattr(catalog_store, "_storage_client", lambda: FakeClient())
    for var in ("CATALOG_LOCAL_DIR", "SONG_INDEX_LOCAL_DIR", "VOICE_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CATALOG_DIR", str(tmp_path / "dl"))
    get_backend_settings.cache_clear()
    yield
    get_backend_settings.cache_clear()


def test_lifespan_starts_without_voice_db(fresh_singletons: None, caplog: pytest.LogCaptureFixture) -> None:
    from backend.main import app

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert get_voice_data_store().status == "warming_up"
    assert not [r for r in _errors(caplog) if "voice" in r.getMessage().lower()]


def test_lifespan_loads_local_voice_db(
    fresh_singletons: None, monkeypatch: pytest.MonkeyPatch, fixture_db: Path
) -> None:
    monkeypatch.setenv("VOICE_LOCAL_DIR", str(fixture_db.parent))
    get_backend_settings.cache_clear()
    from backend.main import app

    with TestClient(app):
        assert get_voice_data_store().status == "ready"
        assert get_voice_data_store().run == "fixture"


def test_rollback_to_previous_run_keeps_its_files(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, fixture_db: Path
) -> None:
    """A -> B -> A: the stale reader's dir is the new current reader's dir, so it must survive."""
    bucket = FakeBucket()
    bucket.publish(fixture_db, RUN_A)
    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient(bucket), now=0.0)
    bucket.publish(_small_db(tmp_path / "b" / "voice.db", RUN_B), RUN_B)
    assert store.maybe_refresh(1000.0) and store.run == RUN_B
    bucket.publish(fixture_db, RUN_A)
    assert store.maybe_refresh(2000.0) and store.run == RUN_A
    assert (tmp_path / "dl" / "voice" / RUN_A / "voice.db").is_file()
    assert (tmp_path / "dl" / "voice" / RUN_B / "voice.db").is_file()
    assert store.require().song(fixtures.STARLIGHT) is not None  # the live reader still reads run A
    # ... and again forward: B's dir is the live one now; the stale A reader's dir is the old current
    bucket.publish(_small_db(tmp_path / "b2" / "voice.db", RUN_B), RUN_B)
    assert store.maybe_refresh(3000.0) and store.run == RUN_B
    assert store.require().pool_keys() == ["alpha|one", "beta|two"]
    assert (tmp_path / "dl" / "voice" / RUN_A / "voice.db").is_file()  # retired reader still serves A


def test_load_never_raises(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = new_store()

    def boom() -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(store, "_try_update", boom)
    store.load(_settings(tmp_path), client=FakeClient())
    assert store.status == "warming_up"


def test_unreadable_meta_closes_the_new_reader(
    new_store: Callable[[], VoiceDataStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _small_db(tmp_path / "local" / "voice.db", "small")
    closed: list[bool] = []
    real_close = voice_data_store.VoiceReader.close

    def meta(self: voice_data_store.VoiceReader) -> dict[str, str]:
        raise sqlite3.DatabaseError("broken meta")

    def close(self: voice_data_store.VoiceReader) -> None:
        closed.append(True)
        real_close(self)

    monkeypatch.setattr(voice_data_store.VoiceReader, "meta", meta)
    monkeypatch.setattr(voice_data_store.VoiceReader, "close", close)
    store = new_store()
    store.load(_settings(tmp_path, voice_local_dir=str(tmp_path / "local")))
    assert store.status == "warming_up" and closed == [True]
