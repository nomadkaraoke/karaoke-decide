"""A 50k-song user's launch must not read 50k documents, and must stay exact while doing so."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_firestore, get_settings
from backend.api.routes.internal import router as internal_router
from backend.config import BackendSettings
from backend.services import user_songs_snapshot as uss
from backend.services.catalog_store import CatalogUnavailable
from backend.services.cloud_tasks_service import CloudTasksService
from backend.services.firestore_service import USER_SONGS_SNAPSHOT_META as META
from backend.services.firestore_service import FirestoreService
from backend.services.get_made_service import _own_songs, own_items
from backend.services.music_service_service import known_song_count
from backend.services.recs_v2 import context as ctx_mod
from backend.services.user_songs_cache import HEAVY_ROWS
from backend.services.user_songs_snapshot import SHARDS, UserSongsSnapshots, decode_shards, derive_rows, encode_shards
from karaoke_decide.catalog.reader import CatalogReader

USER = "user_heavy"


class MemoryFs(FirestoreService):
    """FirestoreService over dicts, with the real write path; counts the reads that matter."""

    def __init__(self) -> None:
        super().__init__(BackendSettings())
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.full_reads = 0
        self.shard_reads = 0

    def _c(self, collection: str) -> dict[str, dict[str, Any]]:
        return self.docs.setdefault(collection, {})

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        d = self._c(collection).get(doc_id)
        return None if d is None else {"id": doc_id, **copy.deepcopy(d)}

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        self._c(collection)[doc_id] = {**self._c(collection).get(doc_id, {}), **data} if merge else dict(data)
        await self._committed(collection, [doc_id])

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        if doc_id in self._c(collection):
            return False
        self._c(collection)[doc_id] = dict(data)
        await self._committed(collection, [doc_id])
        return True

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self._c(collection)[doc_id].update(data)
        await self._committed(collection, [doc_id])

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self._c(collection).pop(doc_id, None)
        await self._committed(collection, [doc_id])

    async def batch_set(self, collection: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        self._c(collection).update({k: dict(v) for k, v in docs.items()})
        items = list(docs)
        for start in range(0, len(items), 400):
            await self._committed(collection, items[start : start + 400])

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self._c(collection).pop(doc_id, None)
        for start in range(0, len(doc_ids), 400):
            await self._committed(collection, doc_ids[start : start + 400])

    async def get_many(self, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        if collection == SHARDS:
            self.shard_reads += len(ids)
        return {i: dict(self._c(collection)[i]) for i in ids if i in self._c(collection)}

    async def count_documents(self, collection: str, filters: Any = None) -> int:
        return len(self._rows(collection, filters))

    def _rows(self, collection: str, filters: Any) -> list[dict[str, Any]]:
        out = [{"id": k, **copy.deepcopy(v)} for k, v in self._c(collection).items()]
        for field, _, value in filters or []:
            out = [d for d in out if d.get(field) == value]
        return out

    async def query_documents(  # type: ignore[override]
        self, collection: str, filters: Any = None, select: Any = None, **_: Any
    ) -> list[Any]:
        if collection == "user_songs":
            self.full_reads += 1
        rows = self._rows(collection, filters)
        if select:
            rows = [{k: v for k, v in r.items() if k == "id" or k in select} for r in rows]
        return rows

    async def run_transaction(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        fs = self

        class Tx:
            async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
                d = fs._c(collection).get(doc_id)
                return None if d is None else copy.deepcopy(d)

            def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
                fs._c(collection)[doc_id] = dict(data)

        return await fn(Tx())

    async def _mark_user_songs_snapshot(self, doc_ids: Any) -> None:
        """The Increment / ArrayUnion semantics of the real one, which the emulator test covers."""
        for doc_id in doc_ids:
            meta = self._c(META).get(doc_id.split(":", 1)[0])
            if meta is None:
                continue
            meta["rev"] = int(meta.get("rev") or 0) + 1
            if len(doc_ids) > 50:
                meta["stale"], meta["dirty_ids"] = True, []
            elif doc_id not in meta["dirty_ids"]:
                meta["dirty_ids"].append(doc_id)


class Reader(MagicMock):
    """catalog.db stand-in: even-numbered songs have a karaoke version."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, spec=CatalogReader, **k)
        self.kn_song_keys: Callable[[Any], dict[int, str]] = lambda ids: {}
        self.has_karaoke_key = lambda key: key.endswith(("0", "2", "4", "6", "8"))
        self.match_kn = lambda artist, title: None
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.execute("CREATE TABLE songs (song_key TEXT, artist TEXT, title TEXT)")
        self.conn = lambda: db


def row(i: int, **over: Any) -> dict[str, Any]:
    return {
        "user_id": USER,
        "song_id": f"lastfm:{i}",
        "artist": f"Invented Artist {i % 40}",
        "title": f"Invented Title {i}",
        "source": "lastfm",
        "sources": ["lastfm"],
        "playcount": 1 + i % 50,
        "rank": i + 1,
        **over,
    }


def seed(fs: MemoryFs, n: int = HEAVY_ROWS) -> None:
    fs._c("user_songs").update({f"{USER}:lastfm:{i}": row(i) for i in range(n)})


class Scheduled(list[str]):
    def __call__(self, user_id: str) -> None:
        self.append(user_id)


@pytest.fixture
def fs() -> MemoryFs:
    return MemoryFs()


@pytest.fixture
def snaps() -> UserSongsSnapshots:
    return UserSongsSnapshots(schedule=Scheduled())


async def _view(snaps: UserSongsSnapshots, fs: MemoryFs, run: str = "r1") -> uss.SongsView | None:
    return await snaps._view(fs, Reader(), run, USER)


# ------------------------------------------------------------------ which users


async def test_a_user_under_the_threshold_gets_no_snapshot(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    seed(fs, HEAVY_ROWS - 1)
    assert await _view(snaps, fs) is None
    assert META not in fs.docs or USER not in fs.docs[META]
    assert fs.full_reads == 0


async def test_small_users_are_remembered_so_they_cost_no_extra_queries(
    fs: MemoryFs, snaps: UserSongsSnapshots
) -> None:
    seed(fs, 3)
    calls = 0
    real = fs.get_document

    async def counted(collection: str, doc_id: str) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        return await real(collection, doc_id)

    fs.get_document = counted  # type: ignore[method-assign]
    await _view(snaps, fs)
    await _view(snaps, fs)
    assert calls == 1


async def test_a_fake_firestore_reads_rows_the_normal_way(snaps: UserSongsSnapshots) -> None:
    assert await snaps.view(MagicMock(), Reader(), "r1", USER) is None


async def test_any_snapshot_failure_falls_back_to_reading_rows(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("firestore down")

    fs.get_document = boom  # type: ignore[method-assign]
    assert await snaps.view(fs, Reader(), "r1", USER) is None


# ------------------------------------------------------------------ the point: no full read


async def test_first_read_builds_then_no_request_reads_every_row_again(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    first = await _view(snaps, fs)
    assert first is not None and fs.full_reads == 1

    # another instance, or this one after a restart, reads shards rather than rows
    other = UserSongsSnapshots(schedule=Scheduled())
    second = await _view(other, fs)
    assert second is not None and second.known_songs == first.known_songs
    assert fs.full_reads == 1
    assert fs.shard_reads == fs.docs[META][USER]["shards"]

    # held in memory: not even the shards
    shard_reads = fs.shard_reads
    assert await _view(other, fs) is second
    assert fs.shard_reads == shard_reads


async def test_small_shards_split_and_rejoin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uss, "SHARD_BYTES", 100)
    rows = [row(i) for i in range(200)]
    shards = encode_shards(rows)
    assert len(shards) > 1 and all(len(s) <= 100 for s in shards)
    assert decode_shards(shards) == rows


async def test_a_snapshot_row_never_needs_the_catalog_again(fs: MemoryFs) -> None:
    rows = [{"id": f"{USER}:1", **row(1)}, {"id": f"{USER}:2", **row(2)}, {"id": f"{USER}:3", "source": "lastfm"}]
    reader = Reader()
    derive_rows(reader, rows)
    assert rows[1]["_hk"] is True and rows[0]["_hk"] is False
    assert rows[2]["_k"] is None

    def fail(*_: Any) -> None:
        raise AssertionError("the catalog was queried for a derived row")

    reader.has_karaoke_key = reader.match_kn = fail
    asked: list[Any] = []

    def kn_song_keys(ids: Any) -> dict[int, str]:
        asked.extend(ids)
        return {}

    reader.kn_song_keys = kn_song_keys
    assert ctx_mod.row_key(reader, rows[1], {}) == (rows[1]["_k"], True)
    assert ctx_mod.row_key(reader, rows[2], {}) is None
    ctx_mod.row_kn_keys(reader, [{**rows[0], "song_id": "123"}])
    assert asked == []


# ------------------------------------------------------------------ exactness


async def test_the_view_is_exactly_what_the_rows_give(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    seed(fs)
    fs._c("user_songs").update(
        {
            f"{USER}:a": row(90001, loved=True, source="manual", sources=["manual"]),
            f"{USER}:b": row(90002, sung_feel_latest="bad", times_sung=1),
            f"{USER}:c": row(90003, sung_feel_latest="great", times_sung=2, sung_key_latest="too_high"),
            f"{USER}:d": row(90004, session_would_sing="no"),
            f"{USER}:e": row(90005, source="voice_session", sources=["voice_session"], session_feel="fine"),
            f"{USER}:f": {"user_id": USER, "source": "lastfm"},
        }
    )
    reader = Reader()
    plain = await fs.query_documents("user_songs", filters=[("user_id", "==", USER)])
    await snaps._view(fs, reader, "r1", USER)
    view = await UserSongsSnapshots(schedule=Scheduled())._view(fs, reader, "r1", USER)
    assert view is not None
    want = ctx_mod._map_songs(reader, plain)
    got = view.song_map
    assert got.known == want.known
    assert sorted(got.no_karaoke) == sorted(want.no_karaoke)
    assert got.songs_per_artist == want.songs_per_artist
    assert (got.session_no, got.survey_no, got.go_tos) == (want.session_no, want.survey_no, want.go_tos)
    assert got.key_hints == want.key_hints and got.own_key_answered == want.own_key_answered
    assert view.names == ctx_mod._artist_names(plain)
    assert view.known_songs == known_song_count(plain)
    assert view.source_counts["lastfm"] == sum(d.get("source") == "lastfm" for d in plain)


async def test_get_made_items_match_the_rows_path(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    seed(fs)
    reader = Reader()
    plain = await fs.query_documents("user_songs", filters=[("user_id", "==", USER)])
    unresolved = [{"artist": "Unlisted Band", "title": "Unlisted Tune Number 1", "times_sung": 2}]
    reader.has_karaoke_key = lambda key: key.endswith(("0", "2", "4", "6", "8")) and "unlisted" not in key
    view = await snaps._view(fs, reader, "r1", USER)
    assert view is not None
    got, _ = own_items(reader, view.own_rows, unresolved)
    want, _ = _own_songs(reader, plain, unresolved)
    # shared state isn't touched by a request's unresolved songs
    again, _ = own_items(reader, view.own_rows, [])
    assert sorted(i.key for i in got) == sorted(i.key for i in want)
    assert {i.key: (i.why, i.weight) for i in got} == {i.key: (i.why, i.weight) for i in want}
    assert len(again) == len(got) - 1


async def test_a_single_write_is_seen_at_once_by_every_instance_without_a_full_read(fs: MemoryFs) -> None:
    seed(fs)
    a, b = UserSongsSnapshots(schedule=Scheduled()), UserSongsSnapshots(schedule=Scheduled())
    await _view(a, fs)
    before = await _view(b, fs)
    assert before is not None
    reads = fs.full_reads

    await fs.set_document("user_songs", f"{USER}:new", row(70001, loved=True))
    for instance in (a, b):
        view = await _view(instance, fs)
        assert view is not None and view.known_songs == before.known_songs + 1
    assert fs.full_reads == reads


async def test_a_deleted_row_leaves_the_view(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    before = await _view(snaps, fs)
    assert before is not None
    await fs.delete_document("user_songs", f"{USER}:lastfm:0")
    after = await _view(snaps, fs)
    assert after is not None and after.row_count == before.row_count - 1


async def test_a_rewritten_row_replaces_its_old_self(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    await _view(snaps, fs)
    await fs.set_document("user_songs", f"{USER}:lastfm:1", row(1, sung_feel_latest="bad", times_sung=1))
    view = await _view(snaps, fs)
    assert view is not None and view.row_count == HEAVY_ROWS
    assert len(view.song_map.survey_no) == 1


async def test_a_bulk_write_marks_stale_and_asks_for_a_rebuild_while_serving(fs: MemoryFs) -> None:
    seed(fs)
    scheduled = Scheduled()
    snaps = UserSongsSnapshots(schedule=scheduled)
    await _view(snaps, fs)
    await fs.batch_set("user_songs", {f"{USER}:bulk:{i}": row(80000 + i) for i in range(60)})
    assert fs.docs[META][USER]["stale"] is True

    served = await _view(snaps, fs)
    assert served is not None and served.row_count == HEAVY_ROWS
    assert scheduled == [USER] and fs.full_reads == 1

    assert await snaps.refresh(fs, Reader(), "r1", USER) == "rebuilt"
    view = await _view(snaps, fs)
    assert view is not None and view.row_count == HEAVY_ROWS + 60
    assert fs.docs[META][USER]["stale"] is False


async def test_writes_during_a_rebuild_stay_dirty(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    await _view(snaps, fs)
    meta = await fs.get_document(META, USER)
    assert meta is not None
    rows = await snaps._current_rows(fs, Reader(), USER, meta)
    await fs.set_document("user_songs", f"{USER}:mid", row(70002, loved=True))  # after the read, before publish
    await snaps._publish(fs, USER, meta, rows, "r1", time.time(), time.time())
    assert fs.docs[META][USER]["dirty_ids"] == [f"{USER}:mid"]
    view = await _view(snaps, fs)
    assert view is not None and view.row_count == HEAVY_ROWS + 1


async def test_publishing_replaces_the_shards(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    await _view(snaps, fs)
    old = set(fs.docs[SHARDS])
    fs.docs[META][USER]["stale"] = True
    await snaps.refresh(fs, Reader(), "r1", USER)
    new = set(fs.docs[SHARDS])
    assert new and not (old & new)


async def test_shards_replaced_mid_read_fall_back_to_rows(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    seed(fs)
    await _view(snaps, fs)
    fs.docs[SHARDS].clear()
    assert await UserSongsSnapshots(schedule=Scheduled()).view(fs, Reader(), "r1", USER) is None


# ------------------------------------------------------------------ upkeep


async def test_many_dirty_rows_compact_without_reading_every_row(fs: MemoryFs) -> None:
    seed(fs)
    scheduled = Scheduled()
    snaps = UserSongsSnapshots(schedule=scheduled)
    await _view(snaps, fs)
    for i in range(uss.DIRTY_COMPACT_AT + 1):
        await fs.set_document("user_songs", f"{USER}:d{i}", row(60000 + i))
    await _view(snaps, fs)
    assert scheduled == [USER]
    reads = fs.full_reads
    assert await snaps.refresh(fs, Reader(), "r1", USER) == "compacted"
    assert fs.full_reads == reads and fs.docs[META][USER]["dirty_ids"] == []
    view = await _view(snaps, fs)
    assert view is not None and view.row_count == HEAVY_ROWS + uss.DIRTY_COMPACT_AT + 1


async def test_a_new_catalog_is_derived_again_without_reading_every_row(fs: MemoryFs) -> None:
    seed(fs)
    scheduled = Scheduled()
    snaps = UserSongsSnapshots(schedule=scheduled)
    await _view(snaps, fs, "r1")
    await _view(snaps, fs, "r2")
    assert scheduled == [USER]  # keeps serving the old derivation meanwhile
    reads = fs.full_reads

    changed = Reader()
    changed.has_karaoke_key = lambda key: True
    assert await snaps.refresh(fs, changed, "r2", USER) == "compacted"
    assert fs.full_reads == reads and fs.docs[META][USER]["catalog_run"] == "r2"
    view = await snaps._view(fs, changed, "r2", USER)
    assert view is not None and view.song_map.no_karaoke == []


async def test_the_lease_keeps_instances_from_piling_on(fs: MemoryFs) -> None:
    seed(fs)
    scheduled = Scheduled()
    now = [time.time()]
    snaps = UserSongsSnapshots(clock=lambda: now[0], schedule=scheduled)
    await _view(snaps, fs, "r1")
    await _view(snaps, fs, "r2")
    await _view(snaps, fs, "r2")
    assert scheduled == [USER]
    now[0] += uss.REFRESH_LEASE_S + 1
    await _view(snaps, fs, "r2")
    assert scheduled == [USER, USER]


async def test_a_finished_refresh_frees_the_lease_for_a_later_bulk_write(fs: MemoryFs) -> None:
    seed(fs)
    scheduled = Scheduled()
    snaps = UserSongsSnapshots(schedule=scheduled)
    await _view(snaps, fs, "r1")
    await _view(snaps, fs, "r2")
    assert scheduled == [USER]
    await snaps.refresh(fs, Reader(), "r2", USER)

    await fs.batch_set("user_songs", {f"{USER}:bulk:{i}": row(80000 + i) for i in range(60)})
    await _view(snaps, fs, "r2")
    assert scheduled == [USER, USER]


async def test_writes_during_a_refresh_keep_the_lease(fs: MemoryFs) -> None:
    """An import in progress must not trigger one full read after another."""
    seed(fs)
    scheduled = Scheduled()
    snaps = UserSongsSnapshots(schedule=scheduled)
    await _view(snaps, fs, "r1")
    await _view(snaps, fs, "r2")
    meta = await fs.get_document(META, USER)
    assert meta is not None
    rows = await snaps._current_rows(fs, Reader(), USER, meta)
    await fs.batch_set("user_songs", {f"{USER}:bulk:{i}": row(80000 + i) for i in range(60)})
    await snaps._publish(fs, USER, meta, rows, "r2", time.time(), time.time())

    await _view(snaps, fs, "r2")
    assert scheduled == [USER]


async def test_the_local_stacks_inline_task_is_scheduled_on_the_event_loop(
    fs: MemoryFs, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(fs)
    posted: list[str] = []

    class Tasks:
        settings = MagicMock(inline_tasks=True)

        def create_snapshot_refresh_task(self, user_id: str) -> None:
            asyncio.get_running_loop()  # what _run_inline needs: raises in a worker thread
            posted.append(user_id)

    monkeypatch.setattr("backend.services.cloud_tasks_service.get_cloud_tasks_service", Tasks)
    snaps = UserSongsSnapshots()
    await _view(snaps, fs, "r1")
    await _view(snaps, fs, "r2")
    assert posted == [USER]


async def test_an_old_snapshot_reads_every_row_once(fs: MemoryFs) -> None:
    seed(fs)
    now = [time.time()]
    snaps = UserSongsSnapshots(clock=lambda: now[0], schedule=Scheduled())
    await _view(snaps, fs)
    now[0] += uss.MAX_AGE_S + 1
    assert await snaps.refresh(fs, Reader(), "r1", USER) == "rebuilt"
    assert fs.full_reads == 2
    assert await snaps.refresh(fs, Reader(), "r1", USER) == "fresh"
    assert fs.full_reads == 2


async def test_a_failed_schedule_never_fails_the_read(fs: MemoryFs) -> None:
    seed(fs)

    def boom(_: str) -> None:
        raise RuntimeError("tasks down")

    snaps = UserSongsSnapshots(schedule=boom)
    await _view(snaps, fs, "r1")
    assert await _view(snaps, fs, "r2") is not None


async def test_shrinking_under_the_threshold_retires_the_snapshot(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    await _view(snaps, fs)
    fs.docs["user_songs"] = {k: v for k, v in list(fs.docs["user_songs"].items())[:10]}
    fs.docs[META][USER]["stale"] = True
    assert await snaps.refresh(fs, Reader(), "r1", USER) == "rebuilt"
    assert USER not in fs.docs[META] and not fs.docs[SHARDS]
    assert await _view(snaps, fs) is None


async def test_a_deleted_account_does_not_get_its_snapshot_back(fs: MemoryFs) -> None:
    seed(fs)
    snaps = UserSongsSnapshots(schedule=Scheduled())
    await _view(snaps, fs)
    meta = await fs.get_document(META, USER)
    assert meta is not None
    rows = await snaps._current_rows(fs, Reader(), USER, meta)
    fs.docs[META].clear()
    fs.docs[SHARDS].clear()
    await snaps._publish(fs, USER, meta, rows, "r1", time.time(), time.time())
    assert USER not in fs.docs[META] and not fs.docs[SHARDS]


async def test_refresh_without_a_snapshot_does_nothing(fs: MemoryFs, snaps: UserSongsSnapshots) -> None:
    assert await snaps.refresh(fs, Reader(), "r1", USER) == "no_snapshot"


# ------------------------------------------------------------------ the marker


async def test_marker_is_a_no_op_for_a_user_without_a_snapshot() -> None:
    from google.api_core import exceptions as api_exceptions

    fs = FirestoreService(BackendSettings())
    ref = MagicMock()

    async def missing(_: Any) -> None:
        raise api_exceptions.NotFound("no doc")

    ref.update = missing
    fs.collection = MagicMock(return_value=MagicMock(document=MagicMock(return_value=ref)))  # type: ignore[method-assign]
    await fs._mark_user_songs_snapshot([f"{USER}:1"])


async def test_marker_failure_never_fails_the_committed_write() -> None:
    fs = FirestoreService(BackendSettings())
    ref = MagicMock()

    async def down(_: Any) -> None:
        raise RuntimeError("unavailable")

    ref.update = down
    fs.collection = MagicMock(return_value=MagicMock(document=MagicMock(return_value=ref)))  # type: ignore[method-assign]
    await fs._mark_user_songs_snapshot([f"{USER}:1"])


async def test_marker_groups_ids_by_user_and_goes_stale_when_the_list_is_full() -> None:
    from google.api_core import exceptions as api_exceptions

    fs = FirestoreService(BackendSettings())
    updates: dict[str, list[dict[str, Any]]] = {}

    def doc(user_id: str) -> MagicMock:
        ref = MagicMock()

        async def update(data: dict[str, Any]) -> None:
            updates.setdefault(user_id, []).append(data)
            if user_id == "full" and "stale" not in data:
                raise api_exceptions.InvalidArgument("too big")

        ref.update = update
        return ref

    fs.collection = MagicMock(return_value=MagicMock(document=doc))  # type: ignore[method-assign]
    await fs._mark_user_songs_snapshot(["a:1", "a:2", "full:1", *[f"bulk:{i}" for i in range(51)]])
    assert len(updates["a"]) == 1 and "dirty_ids" in updates["a"][0] and "stale" not in updates["a"][0]
    assert updates["full"][-1]["stale"] is True
    assert updates["bulk"][0]["stale"] is True and updates["bulk"][0]["dirty_ids"] == []


async def test_only_user_songs_writes_are_reported() -> None:
    fs = FirestoreService(BackendSettings())
    seen: list[Any] = []

    async def mark(ids: Any) -> None:
        seen.append(ids)

    fs._mark_user_songs_snapshot = mark  # type: ignore[method-assign,assignment]
    await fs._committed("user_artists", ["a:1"])
    assert not seen
    await fs._committed("user_songs", ["a:1"])
    assert seen == [["a:1"]]


# ------------------------------------------------------------------ the Cloud Task


def _route_client(fs: MemoryFs) -> TestClient:
    app = FastAPI()
    app.include_router(internal_router, prefix="/internal")
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_settings] = lambda: BackendSettings(environment="development")
    return TestClient(app)


def test_the_refresh_route_refreshes_and_says_what_it_did(fs: MemoryFs, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def refresh(firestore: Any, reader: Any, run: str | None, user_id: str) -> str:
        calls.append((firestore, reader, run, user_id))
        return "compacted"

    monkeypatch.setattr(uss.SNAPSHOTS, "refresh", refresh)
    reader = Reader()
    catalog = MagicMock(run="r9", require=lambda: reader)
    monkeypatch.setattr("backend.api.routes.internal.get_catalog_store", lambda: catalog)
    r = _route_client(fs).post("/internal/user-songs-snapshot/refresh", json={"user_id": USER})
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": USER, "result": "compacted"}
    assert calls == [(fs, reader, "r9", USER)]


def test_the_refresh_route_answers_503_so_cloud_tasks_retries_while_the_catalog_loads(
    fs: MemoryFs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def warming() -> None:
        raise CatalogUnavailable("warming_up")

    catalog = MagicMock(run=None, require=warming)
    monkeypatch.setattr("backend.api.routes.internal.get_catalog_store", lambda: catalog)
    r = _route_client(fs).post("/internal/user-songs-snapshot/refresh", json={"user_id": USER})
    assert r.status_code == 503


def test_the_refresh_route_rejects_a_caller_without_a_valid_token_in_production(fs: MemoryFs) -> None:
    client = _route_client(fs)
    assert client.app is not None
    client.app.dependency_overrides[get_settings] = lambda: BackendSettings(  # type: ignore[attr-defined]
        environment="production", cloud_run_url="https://example.run.app"
    )
    r = client.post("/internal/user-songs-snapshot/refresh", json={"user_id": USER})
    assert r.status_code in (401, 403)


def test_the_task_posts_to_the_route_the_api_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = MagicMock()
    monkeypatch.setattr("backend.services.cloud_tasks_service.tasks_v2", tasks)
    service = CloudTasksService(BackendSettings(environment="development", cloud_run_url="https://x.run.app"))
    service._client = MagicMock()
    service.create_snapshot_refresh_task(USER)
    http = tasks.CreateTaskRequest.call_args.kwargs["task"]["http_request"]
    assert http["url"] == "https://x.run.app/internal/user-songs-snapshot/refresh"
    assert json.loads(http["body"]) == {"user_id": USER}
    assert "/user-songs-snapshot/refresh" in {getattr(r, "path", "") for r in internal_router.routes}
