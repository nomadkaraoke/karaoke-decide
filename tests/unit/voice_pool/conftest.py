"""Shared fixtures for the voice-pool tests: fixture catalog.db + availability.db, fake BigQuery
and an in-memory async Firestore (invented data only, no GCP)."""

from __future__ import annotations

import copy
import gzip
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.availability import build_availability_db
from karaoke_decide.song_search.build_song_id import build as build_song_id
from karaoke_decide.voice_pool import select
from tests.unit.fake_gcs import FakeBucket

SONG_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"


@pytest.fixture(scope="session")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fixtures.build(tmp_path_factory.mktemp("vp_catalog"))


@pytest.fixture(scope="session")
def availability_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("vp_availability")
    build_song_id([SONG_FIXTURES / "songs_fixture.tsv"], d / "song_id.db", run="20261018-000000")
    for name in ("full", "community"):
        with open(SONG_FIXTURES / f"kn_{name}_fixture.json", "rb") as f, gzip.open(d / f"{name}.json.gz", "wb") as g:
            shutil.copyfileobj(f, g)
    build_availability_db(
        d / "full.json.gz", d / "community.json.gz", d / "song_id.db", d / "availability.db", run="20261018-000000"
    )
    return d / "availability.db"


@pytest.fixture
def reader(catalog_db: Path, availability_db: Path) -> Iterator[CatalogReader]:
    r = CatalogReader(catalog_db, availability_db)
    yield r
    r.close()


class FakeBq:
    """Routes the two voice-pool queries; every kn_id gets an invented Spotify track."""

    def __init__(
        self,
        *,
        durations: dict[int, int] | None = None,
        missing: set[int] | None = None,
        ultrastar: set[str] | None = None,
    ) -> None:
        self.durations = durations or {}
        self.missing = missing or set()
        self.ultrastar = ultrastar or set()
        self.configs: list[Any] = []
        self.sqls: list[str] = []
        self.loads: list[tuple[list[dict[str, Any]], str, Any]] = []

    @staticmethod
    def _param(config: Any, name: str) -> list[Any]:
        return next(p.values for p in config.query_parameters if p.name == name)

    def query(self, sql: str, job_config: Any = None) -> Any:
        self.sqls.append(sql)
        self.configs.append(job_config)
        if sql == select.SPOTIFY_FOR_KN_SQL:
            rows = [
                {
                    "karaoke_id": kn,
                    "spotify_id": f"sp{kn:06d}",
                    "duration_ms": self.durations.get(kn, 200_000),
                    "spotify_pop": kn % 100,
                }
                for kn in self._param(job_config, "kn_ids")
                if kn not in self.missing
            ]
        elif sql == select.ULTRASTAR_SQL:
            rows = [{"song_key": k} for k in self._param(job_config, "song_keys") if k in self.ultrastar]
        else:  # pragma: no cover
            raise AssertionError(f"unexpected query: {sql[:80]}")
        job = MagicMock()
        job.result.side_effect = lambda **_: iter(rows)
        return job

    def load_table_from_json(self, rows: list[dict[str, Any]], table: str, job_config: Any = None) -> Any:
        self.loads.append((list(rows), table, job_config))
        return MagicMock()


@pytest.fixture
def fake_bq() -> type[FakeBq]:
    return FakeBq


class MemoryFs:
    """The async FirestoreService subset the voice-pool queue uses."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}
        self.queries: list[tuple[str, Any, dict[str, Any]]] = []

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        col = self.cols.setdefault(collection, {})
        if doc_id in col:
            return False
        col[doc_id] = copy.deepcopy(data)
        return True

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        col = self.cols.get(collection, {})
        if doc_id not in col:  # Firestore's update() raises NotFound too
            raise KeyError(doc_id)
        col[doc_id].update(copy.deepcopy(data))

    async def transform_document_atomically(self, collection: str, doc_id: str, transform: Any) -> Any:
        col = self.cols.setdefault(collection, {})
        new = transform(copy.deepcopy(col.get(doc_id)))  # may raise to abort without writing
        if new is None:
            col.pop(doc_id, None)
        else:
            col[doc_id] = copy.deepcopy(new)
        return new

    async def count_documents(self, collection: str, filters: Any = None) -> int:
        return len(await self.query_documents(collection, filters))

    async def query_documents(self, collection: str, filters: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.queries.append((collection, filters, kw))
        out = []
        for doc_id, d in self.cols.get(collection, {}).items():
            if all(op == "==" and d.get(f) == v for f, op, v in filters or []):
                out.append({"id": doc_id, **copy.deepcopy(d)})
        return out


@pytest.fixture
def fs() -> MemoryFs:
    return MemoryFs()


class FakeGcs:
    """``storage.Client`` stand-in: one in-memory ``FakeBucket`` per name."""

    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


@pytest.fixture
def gcs() -> FakeGcs:
    return FakeGcs()


class FakeRunner:
    """``JobRunner`` that records runs; ``running`` holds the job names reported as running."""

    def __init__(self, running: set[str] | None = None, fail: dict[str, Exception] | None = None) -> None:
        self.running = running or set()
        self.fail = fail or {}
        self.calls: list[dict[str, Any]] = []

    def run(self, job: str, region: str, args: list[str] | None = None, task_count: int | None = None) -> str:
        if job in self.fail:
            raise self.fail[job]
        self.calls.append({"job": job, "region": region, "args": args, "task_count": task_count})
        return f"projects/p/locations/{region}/jobs/{job}/executions/{job}-{len(self.calls)}"

    def is_running(self, job: str, region: str) -> bool:
        if job in self.fail:
            raise self.fail[job]
        return job in self.running


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


class Clock:
    """Fake ``time.time`` + async sleep: sleeping advances the clock and is recorded."""

    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()
