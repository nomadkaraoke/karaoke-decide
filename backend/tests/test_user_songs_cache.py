"""The shared user_songs read that keeps heavy users from running an instance out of memory.

Incident 2026-11-03: 11 concurrent full reads of one 50k-song user (each ~130 MiB) exceeded 2 GiB.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services import user_songs_cache as usc
from backend.services.firestore_service import FirestoreService
from backend.services.get_made_service import GetMadeService, _own_songs
from backend.services.music_service_service import MusicServiceService
from backend.services.recs_v2 import context as ctx_mod
from backend.services.recs_v2.filters import RecFilters
from backend.services.recs_v2.service import RecommendationServiceV2, RecommendationsV2Response, RecVoice
from backend.services.user_songs_cache import SingleFlight, UserSongsCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeFs:
    """The two ``FirestoreService`` reads the cache uses, with a gate to hold reads in flight."""

    def __init__(self, rows: int = 2, count: int | Exception | None = None) -> None:
        self.rows = rows
        self.count = count
        self.reads: list[str] = []
        self.selects: list[Any] = []
        self.active = 0
        self.max_active = 0
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail: Exception | None = None

    async def count_documents(self, collection: str, filters: Any = None) -> int:
        if isinstance(self.count, Exception):
            raise self.count
        return self.rows if self.count is None else self.count

    async def query_documents(self, collection: str, filters: Any = None, select: Any = None) -> list[dict[str, Any]]:
        assert collection == "user_songs"
        user_id = filters[0][2]
        self.reads.append(user_id)
        self.selects.append(select)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.gate.wait()
            if self.fail:
                raise self.fail
            return [{"id": f"{user_id}:{i}"} for i in range(self.rows)]
        finally:
            self.active -= 1


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


# ------------------------------------------------------------------ SingleFlight


async def test_single_flight_runs_concurrent_callers_once() -> None:
    calls = 0
    gate = asyncio.Event()

    async def work() -> list[int]:
        nonlocal calls
        calls += 1
        await gate.wait()
        return [1]

    flight: SingleFlight[list[int]] = SingleFlight()
    waiters = [asyncio.create_task(flight.run("k", work)) for _ in range(5)]
    await _settle()
    gate.set()
    results = await asyncio.gather(*waiters)
    assert calls == 1
    assert all(r is results[0] for r in results)
    assert await flight.run("k", work) == [1]  # finished flights aren't kept
    assert calls == 2


async def test_single_flight_failure_reaches_every_caller_then_clears() -> None:
    gate = asyncio.Event()

    async def boom() -> int:
        await gate.wait()
        raise RuntimeError("firestore down")

    flight: SingleFlight[int] = SingleFlight()
    waiters = [asyncio.create_task(flight.run("k", boom)) for _ in range(3)]
    await _settle()
    gate.set()
    results = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results)

    async def ok() -> int:
        return 7

    assert await flight.run("k", ok) == 7


async def test_single_flight_one_cancelled_caller_leaves_the_others_running() -> None:
    gate = asyncio.Event()

    async def work() -> str:
        await gate.wait()
        return "done"

    flight: SingleFlight[str] = SingleFlight()
    first = asyncio.create_task(flight.run("k", work))
    second = asyncio.create_task(flight.run("k", work))
    await _settle()
    first.cancel()
    await _settle()
    gate.set()
    assert await second == "done"


async def test_single_flight_abandoned_failure_is_not_logged_as_unretrieved(caplog: pytest.LogCaptureFixture) -> None:
    """asyncio logs "Task exception was never retrieved" at ERROR, which would page #alerts."""
    import gc

    gate = asyncio.Event()

    async def boom() -> int:
        await gate.wait()
        raise RuntimeError("nobody is listening")

    flight: SingleFlight[int] = SingleFlight()
    only = asyncio.create_task(flight.run("k", boom))
    await _settle()
    only.cancel()
    await _settle()
    gate.set()
    await _settle()
    gc.collect()
    assert "never retrieved" not in caplog.text


# ------------------------------------------------------------------ UserSongsCache


async def test_concurrent_readers_of_one_user_share_one_read() -> None:
    """The incident's wave: recs x5, get-made x2 (twice each), summary x2, all for one user."""
    fs = FakeFs()
    fs.gate.clear()
    cache = UserSongsCache()
    waiters = [asyncio.create_task(cache.rows(fs, "u1")) for _ in range(11)]  # type: ignore[arg-type]
    await _settle()
    fs.gate.set()
    results = await asyncio.gather(*waiters)
    assert fs.reads == ["u1"]
    assert all(r is results[0] for r in results)


async def test_read_selects_only_the_fields_consumers_use() -> None:
    fs = FakeFs()
    await UserSongsCache().rows(fs, "u1")  # type: ignore[arg-type]
    assert fs.selects == [usc.USER_SONGS_FIELDS]


async def test_rows_are_reused_within_the_ttl_and_reread_after_it() -> None:
    clock = FakeClock()
    fs = FakeFs()
    cache = UserSongsCache(ttl_s=60, clock=clock)
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    clock.now += 59
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    assert fs.reads == ["u1"]
    clock.now += 2
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    assert fs.reads == ["u1", "u1"]


async def test_expired_rows_are_released_without_another_request() -> None:
    """An idle instance doesn't keep a heavy user's rows until the next request happens along."""
    cache = UserSongsCache(ttl_s=0.01)
    await cache.rows(FakeFs(rows=3), "u1")  # type: ignore[arg-type]
    assert cache.cached_rows == 3
    await asyncio.sleep(0.05)
    assert cache.cached_rows == 0


async def test_a_write_drops_the_cached_rows() -> None:
    fs = FakeFs()
    cache = UserSongsCache()
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    cache.on_write("user_songs", ["u1:123"])
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    assert fs.reads == ["u1", "u1"]
    assert cache.generation("u1") == 1


async def test_writes_to_other_users_and_collections_keep_the_rows() -> None:
    fs = FakeFs()
    cache = UserSongsCache()
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    cache.on_write("user_songs", ["u2:123"])
    cache.on_write("user_artists", ["u1:abc"])
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    assert fs.reads == ["u1"]


async def test_a_write_during_a_read_is_never_hidden_by_that_read() -> None:
    fs = FakeFs()
    fs.gate.clear()
    cache = UserSongsCache()
    before = asyncio.create_task(cache.rows(fs, "u1"))  # type: ignore[arg-type]
    await _settle()
    cache.on_write("user_songs", ["u1:123"])
    after = asyncio.create_task(cache.rows(fs, "u1"))  # type: ignore[arg-type]  # must not join the older read
    await _settle()
    assert fs.reads == ["u1", "u1"]
    fs.gate.set()
    await asyncio.gather(before, after)
    await cache.rows(fs, "u1")  # type: ignore[arg-type]  # the post-write read is the one cached
    assert fs.reads == ["u1", "u1"]


async def test_only_the_older_read_finishing_does_not_populate_the_cache() -> None:
    fs = FakeFs()
    fs.gate.clear()
    cache = UserSongsCache()
    before = asyncio.create_task(cache.rows(fs, "u1"))  # type: ignore[arg-type]
    await _settle()
    cache.on_write("user_songs", ["u1:123"])
    fs.gate.set()
    await before
    assert cache.cached_rows == 0


async def test_heavy_reads_for_different_users_are_limited() -> None:
    """Several heavy users can't exhaust one instance: the rest wait for a slot."""
    fs = FakeFs(rows=3, count=50_000)
    fs.gate.clear()
    cache = UserSongsCache(heavy_rows=5_000, max_heavy_reads=2)
    waiters = [asyncio.create_task(cache.rows(fs, f"u{i}")) for i in range(6)]  # type: ignore[arg-type]
    await _settle()
    assert fs.active == 2
    fs.gate.set()
    await asyncio.gather(*waiters)
    assert fs.max_active == 2
    assert len(fs.reads) == 6


async def test_light_users_do_not_queue_behind_heavy_ones() -> None:
    heavy = FakeFs(rows=3, count=50_000)
    heavy.gate.clear()
    cache = UserSongsCache(heavy_rows=5_000, max_heavy_reads=1)
    blocked = [asyncio.create_task(cache.rows(heavy, f"h{i}")) for i in range(3)]  # type: ignore[arg-type]
    await _settle()
    assert heavy.active == 1

    light = FakeFs(rows=2, count=2)
    assert len(await asyncio.wait_for(cache.rows(light, "l1"), 1)) == 2  # type: ignore[arg-type]

    heavy.gate.set()
    await asyncio.gather(*blocked)


async def test_a_failed_count_is_treated_as_heavy() -> None:
    fs = FakeFs(rows=2, count=RuntimeError("count failed"))
    fs.gate.clear()
    cache = UserSongsCache(max_heavy_reads=1)
    waiters = [asyncio.create_task(cache.rows(fs, f"u{i}")) for i in range(3)]  # type: ignore[arg-type]
    await _settle()
    assert fs.active == 1
    fs.gate.set()
    assert [len(r) for r in await asyncio.gather(*waiters)] == [2, 2, 2]


async def test_a_failed_read_is_not_cached_and_frees_its_slot() -> None:
    fs = FakeFs(rows=2, count=50_000)
    fs.fail = RuntimeError("firestore down")
    cache = UserSongsCache(max_heavy_reads=1)
    with pytest.raises(RuntimeError):
        await cache.rows(fs, "u1")  # type: ignore[arg-type]
    fs.fail = None
    assert len(await asyncio.wait_for(cache.rows(fs, "u1"), 1)) == 2  # type: ignore[arg-type]


async def test_cached_rows_stay_within_the_budget_oldest_out_first() -> None:
    fs = FakeFs(rows=40)
    cache = UserSongsCache(max_cached_rows=100)
    for user in ("a", "b", "c"):
        await cache.rows(fs, user)  # type: ignore[arg-type]
    assert cache.cached_rows == 80
    await cache.rows(fs, "b")  # type: ignore[arg-type]  # served from cache; "c" is now the newest but one
    assert fs.reads == ["a", "b", "c"]
    await cache.rows(fs, "a")  # type: ignore[arg-type]  # evicted: read again
    assert fs.reads == ["a", "b", "c", "a"]
    assert cache.cached_rows <= 100


async def test_a_user_bigger_than_the_budget_is_served_but_not_retained() -> None:
    fs = FakeFs(rows=200)
    cache = UserSongsCache(max_cached_rows=100)
    assert len(await cache.rows(fs, "huge")) == 200  # type: ignore[arg-type]
    assert cache.cached_rows == 0


async def test_fresh_reads_skip_the_cache_and_the_flight_but_not_the_limit() -> None:
    """Voice sessions read their own just-written labels from any instance."""
    fs = FakeFs(rows=2, count=50_000)
    cache = UserSongsCache(max_heavy_reads=1)
    await cache.rows(fs, "u1")  # type: ignore[arg-type]
    await cache.rows(fs, "u1", fresh=True)  # type: ignore[arg-type]
    assert fs.reads == ["u1", "u1"]

    fs.gate.clear()
    waiters = [asyncio.create_task(cache.rows(fs, "u1", fresh=True)) for _ in range(3)]  # type: ignore[arg-type]
    await _settle()
    assert fs.active == 1  # separate reads, one at a time
    fs.gate.set()
    await asyncio.gather(*waiters)
    assert fs.reads == ["u1"] * 5


async def test_stand_ins_read_straight_through() -> None:
    """Coherence relies on FirestoreService's write hook, which a stand-in doesn't have."""
    fs = MagicMock()
    fs.query_documents = AsyncMock(return_value=[{"id": "x"}])
    assert await usc.read_user_songs(fs, "u1") == [{"id": "x"}]
    assert await usc.read_user_songs(fs, "u1") == [{"id": "x"}]
    assert fs.query_documents.await_count == 2
    assert "select" not in fs.query_documents.await_args.kwargs


# ------------------------------------------------------------------ FirestoreService write hook + select


@pytest.fixture
def service() -> FirestoreService:
    svc = FirestoreService(MagicMock(spec=BackendSettings))
    svc._client = MagicMock()
    return svc


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str]]]:
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "backend.services.firestore_service._write_listeners", [lambda c, ids: seen.append((c, list(ids)))]
    )
    return seen


async def test_every_write_method_reports_its_documents(
    service: FirestoreService, writes: list[tuple[str, list[str]]]
) -> None:
    doc = service._client.collection.return_value.document.return_value  # type: ignore[union-attr]
    doc.set, doc.update, doc.delete, doc.create = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    batch = service._client.batch.return_value  # type: ignore[union-attr]
    batch.commit = AsyncMock()

    await service.set_document("user_songs", "u:1", {})
    await service.create_document("user_songs", "u:2", {})
    await service.update_document("user_songs", "u:3", {})
    await service.delete_document("user_songs", "u:4")
    await service.batch_set("user_songs", {"u:5": {}})
    await service.batch_delete("user_songs", ["u:6"])
    assert writes == [("user_songs", [f"u:{i}"]) for i in range(1, 7)]


async def test_a_failed_write_reports_nothing(service: FirestoreService, writes: list[tuple[str, list[str]]]) -> None:
    doc = service._client.collection.return_value.document.return_value  # type: ignore[union-attr]
    doc.set = AsyncMock(side_effect=RuntimeError("denied"))
    with pytest.raises(RuntimeError):
        await service.set_document("user_songs", "u:1", {})
    assert writes == []


async def test_transaction_writes_are_reported_only_after_the_commit(
    service: FirestoreService, writes: list[tuple[str, list[str]]]
) -> None:
    """Reporting before the commit would let a re-read cache the pre-write rows."""
    txn = MagicMock()
    txn._max_attempts = 1
    txn._id = None
    txn._read_only = False
    txn._begin = AsyncMock()
    txn._commit = AsyncMock(return_value=[])
    txn._rollback = AsyncMock()
    txn._clean_up = MagicMock()
    service._client.transaction.return_value = txn  # type: ignore[union-attr]
    seen_during: list[int] = []

    async def fn(view: Any) -> str:
        view.set("user_songs", "u:1", {"a": 1})
        view.delete("user_songs", "u:2")
        seen_during.append(len(writes))
        return "ok"

    assert await service.run_transaction(fn) == "ok"
    assert seen_during == [0]
    assert writes == [("user_songs", ["u:1"]), ("user_songs", ["u:2"])]


async def test_query_documents_applies_select(service: FirestoreService) -> None:
    query = service._client.collection.return_value  # type: ignore[union-attr]
    query.where.return_value = query
    query.select.return_value = query
    snap = MagicMock(id="u:1")
    snap.to_dict.return_value = {"artist": "A"}

    async def stream() -> Any:
        yield snap

    query.stream = stream
    rows = await service.query_documents("user_songs", filters=[("user_id", "==", "u")], select=["artist"])
    query.select.assert_called_once_with(["artist"])
    assert rows == [{"id": "u:1", "artist": "A"}]


def test_the_shared_cache_listens_to_firestore_writes() -> None:
    from backend.services import firestore_service

    assert usc.USER_SONGS.on_write in firestore_service._write_listeners


# ------------------------------------------------------------------ the projection covers every consumer


class _Recording(dict[str, Any]):
    """A row noting which fields consumers ask for, and on which thread."""

    read: set[str] = set()
    threads: set[int] = set()

    def get(self, key: str, default: Any = None) -> Any:
        _Recording.read.add(key)
        _Recording.threads.add(threading.get_ident())
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:
        _Recording.read.add(key)
        _Recording.threads.add(threading.get_ident())
        return super().__getitem__(key)


def _rich_rows() -> list[dict[str, Any]]:
    base = {
        "user_id": "u1",
        "artist": "An Artist",
        "title": "A Title",
        "playcount": 4,
        "rank": 3,
        "times_sung": 1,
        "session_feel": "fine",
        "session_would_sing": "yes",
        "sung_feel_latest": "ok",
        "sung_key_latest": "too_high",
    }
    return [
        _Recording({**base, "song_id": "123", "source": "spotify", "sources": ["spotify", "lastfm"], "loved": True}),
        _Recording({**base, "song_id": "sk:x", "song_key": "a|b", "source": "lastfm", "title": "B"}),
        _Recording({**base, "song_id": "abc", "source": "voice_session", "sources": ["voice_session"], "title": "C"}),
    ]


def _reader() -> MagicMock:
    reader = MagicMock()
    reader.kn_song_keys.return_value = {123: "kn|song"}
    reader.has_karaoke_key.return_value = False
    reader.match_kn.return_value = None
    return reader


@pytest.fixture
def recording() -> None:
    _Recording.read = set()
    _Recording.threads = set()


@pytest.mark.usefixtures("recording")
def test_projection_covers_recs_context_fields() -> None:
    ctx_mod._map_songs(_reader(), _rich_rows())
    ctx_mod._artist_names(_rich_rows())
    assert _Recording.read <= set(usc.USER_SONGS_FIELDS), _Recording.read - set(usc.USER_SONGS_FIELDS)


@pytest.mark.usefixtures("recording")
def test_projection_covers_get_made_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.services.get_made_service.song_titles", lambda *_: {})
    _own_songs(_reader(), _rich_rows(), [])
    assert _Recording.read <= set(usc.USER_SONGS_FIELDS), _Recording.read - set(usc.USER_SONGS_FIELDS)


@pytest.mark.usefixtures("recording")
async def test_known_songs_count_uses_projected_fields_off_the_event_loop() -> None:
    fs = MagicMock()
    fs.query_documents = AsyncMock(return_value=_rich_rows())
    svc = MusicServiceService.__new__(MusicServiceService)
    svc.firestore = fs
    assert await svc.count_known_songs("u1") >= 1
    assert _Recording.read <= set(usc.USER_SONGS_FIELDS), _Recording.read - set(usc.USER_SONGS_FIELDS)
    assert threading.get_ident() not in _Recording.threads


@pytest.mark.usefixtures("recording")
async def test_artist_names_are_built_off_the_event_loop() -> None:
    fs = MagicMock()
    fs.query_documents = AsyncMock(side_effect=lambda c, **_: _rich_rows() if c == "user_songs" else [])
    feedback = MagicMock()
    feedback.hidden_keys = AsyncMock(return_value=set())
    fs.get_document = AsyncMock(return_value={})
    await ctx_mod.build_context(fs, _reader(), feedback, "guest_1")
    assert threading.get_ident() not in _Recording.threads


# ------------------------------------------------------------------ recs + get-made use it


class _Computed:
    calls = 0


def _recs_service() -> tuple[RecommendationServiceV2, _Computed]:
    svc = RecommendationServiceV2(MagicMock(), MagicMock(), MagicMock())
    compute = _Computed()

    async def slow(*_: Any, **__: Any) -> RecommendationsV2Response:
        compute.calls += 1
        await asyncio.sleep(0.01)
        return RecommendationsV2Response(
            sections=[],
            filters_applied={},
            next_cursor=None,
            has_profile=False,
            catalog_run="r",
            voice=RecVoice(has_profile=False, method=None),
        )

    svc._recommend = slow  # type: ignore[method-assign]
    return svc, compute


async def test_identical_concurrent_recs_compute_once() -> None:
    """Discover, the Me go-tos shelf and get-made all ask for the same unfiltered recs at launch."""
    from karaoke_decide.core.models import User

    svc, compute = _recs_service()
    user = User(id="u-identical")
    results = await asyncio.gather(*(svc.recommend(user, RecFilters()) for _ in range(5)))
    assert compute.calls == 1
    assert all(r is results[0] for r in results)


async def test_recs_with_different_requests_compute_separately() -> None:
    from karaoke_decide.core.models import User

    svc, compute = _recs_service()
    user = User(id="u-different")
    await asyncio.gather(
        svc.recommend(user, RecFilters()),
        svc.recommend(user, RecFilters(classic=True)),
        svc.recommend(user, RecFilters(), "fit"),
        svc.recommend(User(id="someone-else"), RecFilters()),
    )
    assert compute.calls == 4


async def test_a_write_starts_a_new_recs_flight() -> None:
    from karaoke_decide.core.models import User

    svc, compute = _recs_service()
    user = User(id="u-writes")
    first = asyncio.create_task(svc.recommend(user, RecFilters()))
    await asyncio.sleep(0)
    usc.USER_SONGS.on_write("user_songs", ["u-writes:1"])
    await asyncio.gather(first, svc.recommend(user, RecFilters()))
    assert compute.calls == 2


async def test_recs_for_a_profile_override_never_share() -> None:
    """A voice session's result is computed for that session's unsaved profile, on fresh rows."""
    from karaoke_decide.core.models import User

    svc, compute = _recs_service()
    profile = MagicMock()
    user = User(id="u-profile")
    await asyncio.gather(*(svc.recommend(user, RecFilters(), profile=profile) for _ in range(3)))
    assert compute.calls == 3


async def test_a_bad_cursor_is_rejected_before_any_flight() -> None:
    from backend.services.recs_v2.service import BadCursorError
    from karaoke_decide.core.models import User

    svc, compute = _recs_service()
    with pytest.raises(BadCursorError):
        await svc.recommend(User(id="u-cursor"), RecFilters(), "fit", "not-a-cursor!")
    assert compute.calls == 0


@pytest.mark.parametrize(
    ("kwargs", "fresh"),
    [({}, True), ({"shared": False}, True), ({"shared": True}, False)],
)
async def test_build_context_reads_fresh_unless_asked_to_share(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], fresh: bool
) -> None:
    seen: list[bool] = []

    async def read(_fs: Any, _user: str, **read_kwargs: bool) -> list[dict[str, Any]]:
        seen.append(read_kwargs["fresh"])
        return []

    monkeypatch.setattr(ctx_mod, "read_user_songs", read)
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value={})
    fs.query_documents = AsyncMock(return_value=[])
    feedback = MagicMock()
    feedback.hidden_keys = AsyncMock(return_value=set())
    await ctx_mod.build_context(fs, _reader(), feedback, "guest_1", **kwargs)
    # Default stays fresh for voice sessions and the voice pool; only recs asks to share.
    assert seen == [fresh]


async def test_get_made_reads_through_the_shared_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def read(_fs: Any, user_id: str, *, fresh: bool = False) -> list[dict[str, Any]]:
        seen.append(user_id)
        return []

    monkeypatch.setattr("backend.services.get_made_service.read_user_songs", read)
    fs = MagicMock()
    fs.query_documents = AsyncMock(return_value=[])
    store = MagicMock()
    store.require.return_value = _reader()
    monkeypatch.setattr("backend.services.get_made_service.song_titles", lambda *_: {})
    recs = MagicMock()
    recs.recommend = AsyncMock(side_effect=RuntimeError("no recs"))
    await GetMadeService(fs, store, recs).list("u1")
    assert seen == ["u1"]
