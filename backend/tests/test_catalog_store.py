"""CatalogStore: loads catalog.db (+ availability.db) at startup and never raises."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.config import BackendSettings
from backend.services.catalog_store import CatalogStore, CatalogUnavailable, get_catalog_store
from backend.tests.search.conftest import RUN_ID
from karaoke_decide.catalog import fixtures

POINTER_RUN = "20261021-010101"


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket = bucket
        self.name = name

    def _data(self) -> bytes:
        self.bucket.reads.append(self.name)
        if self.bucket.delay:
            time.sleep(self.bucket.delay)
        if self.bucket.error is not None:
            raise self.bucket.error
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)  # stands in for google.api_core NotFound
        return self.bucket.objects[self.name]

    def download_as_bytes(self) -> bytes:
        return self._data()

    def download_to_filename(self, filename: str) -> None:
        Path(filename).write_bytes(self._data())

    def open(self, mode: str = "rb", **kwargs: Any) -> io.BytesIO:
        assert mode == "rb"
        return io.BytesIO(self._data())


class FakeBucket:
    def __init__(self, delay: float = 0.0, error: Exception | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.delay = delay
        self.error = error
        self.reads: list[str] = []

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class ForbiddenError(Exception):
    """Stands in for google.api_core.exceptions.Forbidden."""

    code = 403


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


class FakeClient:
    def __init__(self, buckets: dict[str, FakeBucket]) -> None:
        self.buckets = buckets

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


@pytest.fixture
def new_store() -> Iterator[Callable[[], CatalogStore]]:
    """Fresh stores whose SQLite connections are closed after the test."""
    created: list[CatalogStore] = []

    def make() -> CatalogStore:
        created.append(CatalogStore())
        return created[-1]

    yield make
    for store in created:
        store.close()


@pytest.fixture(scope="module")
def catalog_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("catalog-local")
    fixtures.build(d)
    return d


def _settings(tmp_path: Path, **kw: Any) -> BackendSettings:
    base: dict[str, Any] = {"catalog_dir": str(tmp_path / "dl"), "catalog_load_timeout_s": 10}
    base.update(kw)
    return BackendSettings(**base)


def _published(catalog_dir: Path, index_dir: Path, *, sha: str | None = None) -> FakeClient:
    db = (catalog_dir / "catalog.db").read_bytes()
    cat = FakeBucket()
    path = f"catalog/{POINTER_RUN}/catalog.db.gz"
    cat.objects[path] = gzip.compress(db)
    cat.objects["catalog/latest.json"] = json.dumps(
        {"run": POINTER_RUN, "path": path, "sha256": sha or hashlib.sha256(db).hexdigest(), "bytes": len(db)}
    ).encode()
    idx = FakeBucket()
    for name in ("song_id.db", "availability.db", "manifest.json"):
        idx.objects[f"song-index/{RUN_ID}/{name}"] = (index_dir / name).read_bytes()
    idx.objects["song-index/latest.json"] = json.dumps({"run": "20261018-000000", "token": "0123456789abcdef"}).encode()
    return FakeClient({"nomadkaraoke-musicbrainz-data": cat, "nomadkaraoke-kn-data": idx})


def test_local_dirs_load(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    store = new_store()
    store.load(_settings(tmp_path, catalog_local_dir=str(catalog_dir), song_index_local_dir=str(index_dir)))
    assert store.status == "ready"
    assert store.run == "fixture"
    reader = store.require()
    assert reader is store.reader and reader.has_availability
    assert reader.match_kn("Toto", "Africa") is not None


def test_gcs_load_downloads_verifies_and_attaches(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    store = new_store()
    store.load(_settings(tmp_path), client=_published(catalog_dir, index_dir))
    assert store.status == "ready" and store.run == POINTER_RUN
    reader = store.require()
    assert reader.has_availability
    assert reader.conn().execute("SELECT COUNT(*) FROM av.songs").fetchone()[0] > 0
    assert (tmp_path / "dl" / POINTER_RUN / "catalog.db").read_bytes() == (catalog_dir / "catalog.db").read_bytes()
    # Only availability.db is fetched from the search index (song_id.db is the search service's).
    assert not list((tmp_path / "dl").rglob("song_id.db"))
    assert not list((tmp_path / "dl").rglob("*.part"))


def test_startup_without_catalog(
    new_store: Callable[[], CatalogStore], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path), client=FakeClient({}))
    assert store.status == "warming_up" and store.reader is None and store.run is None
    assert not _errors(caplog)
    with pytest.raises(CatalogUnavailable):
        store.require()
    # Expected before the first build: WARNING, never ERROR (ERROR pages #alerts).
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_download_timeout(new_store: Callable[[], CatalogStore], tmp_path: Path) -> None:
    store = new_store()
    hung = FakeClient(
        {"nomadkaraoke-musicbrainz-data": FakeBucket(delay=5), "nomadkaraoke-kn-data": FakeBucket(delay=5)}
    )
    started = time.monotonic()
    store.load(_settings(tmp_path, catalog_load_timeout_s=0.5), client=hung)
    assert time.monotonic() - started < 1.5
    assert store.status == "warming_up"


def test_corrupt_catalog(
    new_store: Callable[[], CatalogStore],
    tmp_path: Path,
    catalog_dir: Path,
    index_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path), client=_published(catalog_dir, index_dir, sha="0" * 64))
    assert store.status == "warming_up"
    assert not list((tmp_path / "dl").rglob("catalog.db*"))
    # A published but unusable artifact is page-worthy: exactly one ERROR for the attempt.
    assert len(_errors(caplog)) == 1 and "sha256" in _errors(caplog)[0].getMessage()


def test_not_gzip_catalog(
    new_store: Callable[[], CatalogStore],
    tmp_path: Path,
    catalog_dir: Path,
    index_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _published(catalog_dir, index_dir)
    client.buckets["nomadkaraoke-musicbrainz-data"].objects[f"catalog/{POINTER_RUN}/catalog.db.gz"] = b"not gzip"
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path), client=client)
    assert store.status == "warming_up"
    assert len(_errors(caplog)) == 1
    assert not list((tmp_path / "dl").rglob("catalog.db*"))


def test_unreadable_pointer(
    new_store: Callable[[], CatalogStore], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bucket = FakeBucket()
    bucket.objects["catalog/latest.json"] = b"[1, 2]"
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path), client=FakeClient({"nomadkaraoke-musicbrainz-data": bucket}))
    assert store.status == "warming_up"
    assert len(_errors(caplog)) == 1


def test_pointer_forbidden_is_error(
    new_store: Callable[[], CatalogStore], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A 403 (lost IAM) is not "not built yet": one ERROR, still warming_up."""
    client = FakeClient({"nomadkaraoke-musicbrainz-data": FakeBucket(error=ForbiddenError("403 Forbidden"))})
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path), client=client)
    assert store.status == "warming_up"
    assert len(_errors(caplog)) == 1 and "403" in _errors(caplog)[0].getMessage()


def test_no_gcs_client_is_error(
    new_store: Callable[[], CatalogStore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from backend.services import catalog_store

    def no_client() -> None:
        raise RuntimeError("no credentials")

    monkeypatch.setattr(catalog_store, "_storage_client", no_client)
    store = new_store()
    with caplog.at_level(logging.INFO):
        store.load(_settings(tmp_path))
    assert store.status == "warming_up"
    assert len(_errors(caplog)) == 1 and "no GCS client" in _errors(caplog)[0].getMessage()


def test_late_availability_is_attached(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    """The catalog doesn't wait past the budget for availability.db; it attaches when it lands."""
    client = _published(catalog_dir, index_dir)
    client.buckets["nomadkaraoke-kn-data"].delay = 0.4  # 3 reads -> ~1.2 s
    store = new_store()
    started = time.monotonic()
    store.load(_settings(tmp_path, catalog_load_timeout_s=0.6), client=client)
    assert time.monotonic() - started < 1.0
    assert store.status == "ready" and not store.require().has_availability
    deadline = time.monotonic() + 5
    while not store.require().has_availability and time.monotonic() < deadline:
        time.sleep(0.05)
    assert store.require().has_availability
    assert store.require().conn().execute("SELECT COUNT(*) FROM av.songs").fetchone()[0] > 0


def test_require_retries_after_retry_window(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    from backend.services import catalog_store

    empty = FakeClient({})
    store = new_store()
    store.load(_settings(tmp_path), client=empty)
    assert store.status == "warming_up"
    reads = len(empty.bucket("nomadkaraoke-musicbrainz-data").reads)

    # Within the window: no new attempt.
    with pytest.raises(CatalogUnavailable):
        store.require()
    assert len(empty.bucket("nomadkaraoke-musicbrainz-data").reads) == reads

    # The pointer has been published since, and the window has passed: require() loads it in-request.
    published = _published(catalog_dir, index_dir)
    store._client = published
    store._last_attempt_end = time.monotonic() - catalog_store.RETRY_AFTER_S - 1
    reader = store.require()
    assert store.status == "ready" and reader.has_availability and store.run == POINTER_RUN


def test_require_never_runs_two_attempts(new_store: Callable[[], CatalogStore], tmp_path: Path) -> None:
    from backend.services import catalog_store

    client = FakeClient({})
    store = new_store()
    store.load(_settings(tmp_path), client=client)
    reads = len(client.bucket("nomadkaraoke-musicbrainz-data").reads)
    store._last_attempt_end = time.monotonic() - catalog_store.RETRY_AFTER_S - 1
    store._attempt_running = True  # another request's attempt is in flight
    with pytest.raises(CatalogUnavailable):
        store.require()
    assert len(client.bucket("nomadkaraoke-musicbrainz-data").reads) == reads


async def test_require_on_event_loop_retries_in_background(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    import asyncio

    from backend.services import catalog_store

    store = new_store()
    store.load(_settings(tmp_path), client=FakeClient({}))
    store._client = _published(catalog_dir, index_dir)
    store._last_attempt_end = time.monotonic() - catalog_store.RETRY_AFTER_S - 1
    with pytest.raises(CatalogUnavailable):  # doesn't block the loop for the load
        store.require()
    for _ in range(100):
        if store.status == "ready":
            break
        await asyncio.sleep(0.05)
    assert store.status == "ready"


def test_catalog_without_availability_is_ready(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path
) -> None:
    store = new_store()
    store.load(_settings(tmp_path, catalog_local_dir=str(catalog_dir), song_index_local_dir=str(tmp_path / "missing")))
    assert store.status == "ready"
    assert not store.require().has_availability


def test_missing_local_catalog(new_store: Callable[[], CatalogStore], tmp_path: Path, index_dir: Path) -> None:
    store = new_store()
    store.load(_settings(tmp_path, catalog_local_dir=str(tmp_path / "empty"), song_index_local_dir=str(index_dir)))
    assert store.status == "warming_up"


def test_get_catalog_store_is_singleton() -> None:
    assert get_catalog_store() is get_catalog_store()


def test_start_load_runs_in_the_background_and_load_at_startup_joins_it(
    new_store: Callable[[], CatalogStore],
    tmp_path: Path,
    catalog_dir: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """backend.serve starts the download before the app import; the lifespan waits for it."""
    from backend.services import catalog_store

    store = new_store()
    client = _published(catalog_dir, index_dir)
    client.buckets["nomadkaraoke-musicbrainz-data"].delay = 0.3
    monkeypatch.setattr(catalog_store, "_storage_client", lambda: client)
    settings = _settings(tmp_path)
    started = time.monotonic()
    store.start_load(settings)
    store.start_load(settings)  # idempotent: one early load
    assert time.monotonic() - started < 0.2
    assert store.status == "warming_up"
    store.load_at_startup(settings)
    assert store.status == "ready" and store.run == POINTER_RUN
    assert client.buckets["nomadkaraoke-musicbrainz-data"].reads.count("catalog/latest.json") == 1


def test_load_at_startup_without_start_load_loads_now(
    new_store: Callable[[], CatalogStore], tmp_path: Path, catalog_dir: Path, index_dir: Path
) -> None:
    store = new_store()
    store.load_at_startup(_settings(tmp_path, catalog_local_dir=str(catalog_dir), song_index_local_dir=str(index_dir)))
    assert store.status == "ready"
