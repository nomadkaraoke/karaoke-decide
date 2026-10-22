"""Daily ListenBrainz poller (Cloud Run Job ``lb-poll``): new listens fold into the profile."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config import BackendSettings
from backend.services.import_ingest_service import IngestResult, IngestTrack
from backend.services.listenbrainz_poller import ListenBrainzPoller, PollReport, RateGate
from backend.services.music_service_service import MusicServiceError
from backend.tests.test_listenbrainz_oauth import FakeDb
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzListen,
    ListenBrainzRateLimitedError,
)

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
T0 = int((NOW - timedelta(days=1)).timestamp())  # a listen yesterday
SONGS = [("Invented Band", "First Song"), ("Other Act", "Second Song"), ("Fixture Trio", "Third Song")]


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class PollDb(FakeDb):
    async def query_documents(
        self, collection: str, filters: list[tuple[str, str, Any]] | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        rows = []
        for doc_id, d in self.cols.get(collection, {}).items():
            if all(op == "==" and d.get(f) == v for f, op, v in filters or []):
                rows.append({"id": doc_id, **copy.deepcopy(d)})
        return rows


class FakeLB:
    """ListenBrainz ``/user/<name>/listens`` semantics: max_ts / min_ts exclusive; with min_ts only
    the ``count`` listens just after it; always newest first."""

    def __init__(self, time: FakeTime | None = None, seconds_per_call: float = 0.0) -> None:
        self.listens: dict[str, list[ListenBrainzListen]] = {}
        self.calls: list[dict[str, Any]] = []
        self.errors: list[BaseException] = []  # raised (in order) by the next calls
        self.time, self.seconds_per_call = time, seconds_per_call
        self.import_status = "completed"
        self.status_calls: list[tuple[str, str]] = []

    def add(self, user: str, n: int, start: int, songs: list[tuple[str, str]] = SONGS) -> None:
        for i in range(n):
            artist, title = songs[i % len(songs)]
            self.listens.setdefault(user, []).append(
                ListenBrainzListen(artist_name=artist, track_name=title, listened_at=start + i)
            )

    def _tick(self) -> None:
        if self.time:
            self.time.t += self.seconds_per_call
        if self.errors:
            raise self.errors.pop(0)

    async def get_listens(
        self, username: str, count: int = 100, max_ts: int | None = None, min_ts: int | None = None
    ) -> list[ListenBrainzListen]:
        self.calls.append({"user": username, "count": count, "max_ts": max_ts, "min_ts": min_ts})
        self._tick()
        rows = [
            x
            for x in self.listens.get(username, [])
            if (max_ts is None or x.listened_at < max_ts) and (min_ts is None or x.listened_at > min_ts)
        ]
        if min_ts is not None and max_ts is None:
            rows = sorted(rows, key=lambda x: x.listened_at)[:count]
        return sorted(rows, key=lambda x: -x.listened_at)[:count]

    async def get_import_status(self, token: str, import_id: str) -> dict[str, Any]:
        self.status_calls.append((token, import_id))
        self._tick()
        return {"status": self.import_status, "progress": "", "metadata": {}}


class FakeIngest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[IngestTrack], str]] = []

    async def ingest(
        self, user_id: str, source: str, tracks: list[IngestTrack], play_mode: str = "max"
    ) -> IngestResult:
        self.calls.append((user_id, source, tracks, play_mode))
        return IngestResult(artists_added=0, songs_added=len(tracks), songs_updated=0, tracks_matched=0)


def _conn(db: PollDb, user: str = "fixture-user", **over: Any) -> str:
    doc_id = f"{user}_listenbrainz"
    db.cols.setdefault("music_services", {})[doc_id] = {
        "id": doc_id,
        "user_id": user,
        "service_type": "listenbrainz",
        "service_username": f"e2e-{user}",
        "auth": "username",
        "created_at": (NOW - timedelta(days=2)).isoformat(),
        **over,
    }
    return doc_id


def _music_service() -> MagicMock:
    ms = MagicMock()
    ms.service_from_doc = MagicMock(side_effect=lambda d: d)
    ms.get_listenbrainz_token = AsyncMock(return_value="meba_token")
    return ms


def _poller(
    db: PollDb, lb: FakeLB, ingest: FakeIngest, time: FakeTime | None = None, ms: MagicMock | None = None
) -> ListenBrainzPoller:
    time = time or FakeTime()
    return ListenBrainzPoller(
        BackendSettings(),
        db,  # type: ignore[arg-type]
        ms or _music_service(),
        ingest,  # type: ignore[arg-type]
        lb,  # type: ignore[arg-type]
        clock=time.clock,
        sleep=time.sleep,
        utcnow=lambda: NOW,
    )


@pytest.fixture
def db() -> PollDb:
    return PollDb()


@pytest.mark.asyncio
async def test_first_poll_backfills_max_mode_and_sets_cursor(db: PollDb) -> None:
    doc_id = _conn(db)
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-fixture-user", 6, T0 - 5)  # each of the 3 songs twice; newest at T0
    lb.add("e2e-fixture-user", 1, T0 - 100, songs=[("invented  BAND", "first song")])  # same identity

    report = await _poller(db, lb, ingest).run(1500)

    assert report == PollReport(users_polled=1, listens_added=7, imports_checked=0, rate_limited=0, remaining=0)
    assert lb.calls[0] == {"user": "e2e-fixture-user", "count": 1000, "max_ts": None, "min_ts": None}
    [(user, source, tracks, mode)] = ingest.calls
    assert (user, source, mode) == ("fixture-user", "listenbrainz", "max")
    by_title = {t.title: t for t in tracks}
    assert len(tracks) == 3
    assert by_title["First Song"].plays == 3 and by_title["First Song"].artist == "Invented Band"
    assert by_title["Second Song"].plays == 2
    assert max(t.last_ts or 0 for t in tracks) == T0
    doc = db.doc("music_services", doc_id)
    assert doc is not None
    assert doc["lb_last_listen_ts"] == T0
    assert doc["lb_last_listen_at"] == datetime.fromtimestamp(T0, UTC).isoformat()
    assert doc["lb_last_polled_at"] == NOW.isoformat()
    assert doc["lb_stale"] is False


@pytest.mark.asyncio
async def test_incremental_poll_adds_plays(db: PollDb) -> None:
    doc_id = _conn(db, lb_last_listen_ts=T0 - 10, lb_last_listen_at="x", lb_last_polled_at=NOW.isoformat())
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-fixture-user", 3, T0 - 20)  # all at or before the cursor: already counted
    lb.add("e2e-fixture-user", 4, T0 - 3)  # new: First, Second, Third, First

    report = await _poller(db, lb, ingest).run(1500)

    assert lb.calls == [{"user": "e2e-fixture-user", "count": 1000, "max_ts": None, "min_ts": T0 - 10}]
    [(_, _, tracks, mode)] = ingest.calls
    assert mode == "add"
    assert {t.title: t.plays for t in tracks} == {"First Song": 2, "Second Song": 1, "Third Song": 1}
    assert report.listens_added == 4
    assert db.doc("music_services", doc_id)["lb_last_listen_ts"] == T0  # type: ignore[index]


@pytest.mark.asyncio
async def test_incremental_poll_no_new_listens_keeps_cursor_and_skips_ingest(db: PollDb) -> None:
    doc_id = _conn(db, lb_last_listen_ts=T0, lb_last_listen_at=datetime.fromtimestamp(T0, UTC).isoformat())
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-fixture-user", 2, T0 - 1)

    report = await _poller(db, lb, ingest).run(1500)

    assert ingest.calls == [] and report.users_polled == 1 and report.listens_added == 0
    doc = db.doc("music_services", doc_id)
    assert doc is not None and doc["lb_last_listen_ts"] == T0 and doc["lb_last_polled_at"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_pagination_until_short_page(db: PollDb) -> None:
    _conn(db, user="first")
    _conn(db, user="later", lb_last_listen_ts=1000)
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-first", 2500, 10_000)
    lb.add("e2e-later", 2500, 2000)

    report = await _poller(db, lb, ingest).run(1500)

    first = [c for c in lb.calls if c["user"] == "e2e-first"]
    assert [c["max_ts"] for c in first] == [None, 10_000 + 1500, 10_000 + 500]  # newest first, 1000 per page
    later = [c for c in lb.calls if c["user"] == "e2e-later"]
    assert [c["min_ts"] for c in later] == [1000, 2000 + 999, 2000 + 1999]  # oldest-new first, cursor moves up
    assert report.listens_added == 5000
    total = {u: sum(t.plays or 0 for t in tracks) for u, _, tracks, _ in ingest.calls}
    assert total == {"first": 2500, "later": 2500}


@pytest.mark.asyncio
async def test_page_caps_first_20_later_10(db: PollDb) -> None:
    _conn(db, user="first")
    _conn(db, user="later", lb_last_listen_ts=0)
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-first", 25_000, 1)
    lb.add("e2e-later", 15_000, 1)

    await _poller(db, lb, ingest).run(10_000)

    assert sum(c["user"] == "e2e-first" for c in lb.calls) == 20
    assert sum(c["user"] == "e2e-later" for c in lb.calls) == 10
    later = db.doc("music_services", "later_listenbrainz")
    assert later is not None and later["lb_last_listen_ts"] == 10_000  # the newest listen it reached


@pytest.mark.asyncio
async def test_429_backoff_then_skip(db: PollDb) -> None:
    _conn(db, user="a", lb_last_polled_at="2026-10-01T00:00:00+00:00")
    _conn(db, user="b", lb_last_polled_at="2026-10-02T00:00:00+00:00")
    time = FakeTime()
    lb, ingest = FakeLB(), FakeIngest()
    lb.add("e2e-a", 2, T0)
    lb.add("e2e-b", 2, T0)
    # a: 429 twice in a row -> skipped. b: one 429, then fine.
    lb.errors = [ListenBrainzRateLimitedError(7), ListenBrainzRateLimitedError(8), ListenBrainzRateLimitedError(9)]

    report = await _poller(db, lb, ingest, time).run(1500)

    assert 7 in time.sleeps and 9 in time.sleeps and 8 not in time.sleeps  # the second 429 is not waited out
    assert report.users_polled == 1 and report.rate_limited == 1
    assert [u for u, *_ in ingest.calls] == ["b"]
    a = db.doc("music_services", "a_listenbrainz")
    assert a is not None and "lb_last_listen_ts" not in a
    assert a["lb_last_polled_at"] == "2026-10-01T00:00:00+00:00"  # stays first in line for tomorrow


@pytest.mark.asyncio
async def test_rate_gate_25_per_minute() -> None:
    time = FakeTime()
    gate = RateGate(per_minute=25, clock=time.clock, sleep=time.sleep)
    stamps = []
    for _ in range(31):
        await gate.wait()
        stamps.append(time.t)
    assert time.t >= 60
    assert stamps[24] == 0 and stamps[25] >= 60
    # Never more than 25 calls inside any 60 s window.
    assert all(sum(s <= x < s + 60 for x in stamps) <= 25 for s in stamps)


@pytest.mark.asyncio
async def test_poller_calls_go_through_the_rate_gate(db: PollDb) -> None:
    for i in range(30):
        _conn(db, user=f"u{i}")
    time = FakeTime()
    lb = FakeLB()
    report = await _poller(db, lb, FakeIngest(), time).run(1500)
    assert report.users_polled == 30 and len(lb.calls) == 30
    assert time.t >= 60


@pytest.mark.asyncio
async def test_stale_after_30_days_and_cleared_on_new_listens(db: PollDb) -> None:
    old = int((NOW - timedelta(days=31)).timestamp())
    doc_id = _conn(
        db,
        created_at=(NOW - timedelta(days=200)).isoformat(),
        lb_last_listen_ts=old,
        lb_last_listen_at=datetime.fromtimestamp(old, UTC).isoformat(),
    )
    lb, ingest = FakeLB(), FakeIngest()

    await _poller(db, lb, ingest).run(1500)
    assert db.doc("music_services", doc_id)["lb_stale"] is True  # type: ignore[index]

    lb.add("e2e-fixture-user", 1, T0)
    await _poller(db, lb, ingest).run(1500)
    assert db.doc("music_services", doc_id)["lb_stale"] is False  # type: ignore[index]


@pytest.mark.asyncio
async def test_never_listened_is_stale_30_days_after_connecting(db: PollDb) -> None:
    fresh = _conn(db, user="fresh", created_at=(NOW - timedelta(days=29)).isoformat())
    old = _conn(db, user="old", created_at=(NOW - timedelta(days=31)).isoformat())
    await _poller(db, FakeLB(), FakeIngest()).run(1500)
    assert db.doc("music_services", fresh)["lb_stale"] is False  # type: ignore[index]
    assert db.doc("music_services", old)["lb_stale"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_budget_stops_and_reports_remaining(db: PollDb) -> None:
    for i in range(3):
        _conn(db, user=f"u{i}", lb_last_polled_at=f"2026-10-0{i + 1}T00:00:00+00:00")
    time = FakeTime()
    lb, ingest = FakeLB(time, seconds_per_call=6), FakeIngest()

    report = await _poller(db, lb, ingest, time).run(10)

    assert report.users_polled == 2 and report.remaining == 1
    assert [c["user"] for c in lb.calls] == ["e2e-u0", "e2e-u1"]


@pytest.mark.asyncio
async def test_order_least_recently_polled_first_missing_first(db: PollDb) -> None:
    _conn(db, user="recent", lb_last_polled_at="2026-10-21T00:00:00+00:00")
    _conn(db, user="never")
    _conn(db, user="older", lb_last_polled_at="2026-10-01T00:00:00+00:00")
    db.cols["music_services"]["x_spotify"] = {"user_id": "x", "service_type": "spotify"}
    lb = FakeLB()
    await _poller(db, lb, FakeIngest()).run(1500)
    assert [c["user"] for c in lb.calls] == ["e2e-never", "e2e-older", "e2e-recent"]


@pytest.mark.asyncio
async def test_pending_import_status_updated(db: PollDb) -> None:
    pending = {"id": "42", "status": "waiting", "submitted_at": "2026-10-21T00:00:00+00:00"}
    doc_id = _conn(db, auth="oauth", lb_pending_import=pending)
    done = _conn(db, user="done", auth="oauth", lb_pending_import={**pending, "id": "7", "status": "completed"})
    lb, ms = FakeLB(), _music_service()

    report = await _poller(db, lb, FakeIngest(), ms=ms).run(1500)

    assert report.imports_checked == 1
    assert lb.status_calls == [("meba_token", "42")]
    stored = db.doc("music_services", doc_id)["lb_pending_import"]  # type: ignore[index]
    assert stored["id"] == "42" and stored["status"] == "completed"
    assert stored["submitted_at"] == pending["submitted_at"] and stored["checked_at"] == NOW.isoformat()
    assert db.doc("music_services", done)["lb_pending_import"]["status"] == "completed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_pending_import_check_failures_never_stop_the_poll(db: PollDb) -> None:
    pending = {"id": "42", "status": "waiting", "submitted_at": "T"}
    a = _conn(db, user="a", auth="oauth", lb_pending_import=pending)
    b = _conn(db, user="b", auth="oauth", lb_pending_import=pending)
    lb, ms = FakeLB(), _music_service()
    lb.add("e2e-a", 1, T0)
    lb.add("e2e-b", 1, T0)
    ms.get_listenbrainz_token.side_effect = [MusicServiceError("needs reconnect"), "meba_b"]
    orig = lb.get_import_status

    async def failing(token: str, import_id: str) -> dict[str, Any]:
        raise ExternalServiceError("ListenBrainz", "API error: 404")

    lb.get_import_status = failing  # type: ignore[method-assign]
    report = await _poller(db, lb, FakeIngest(), ms=ms).run(1500)
    lb.get_import_status = orig  # type: ignore[method-assign]

    assert report.users_polled == 2 and report.imports_checked == 0
    for doc_id in (a, b):
        doc = db.doc("music_services", doc_id)
        assert doc is not None and doc["lb_pending_import"]["status"] == "waiting" and doc["lb_last_listen_ts"] == T0


@pytest.mark.asyncio
async def test_pending_import_replaced_mid_poll_is_not_overwritten(db: PollDb) -> None:
    doc_id = _conn(db, auth="oauth", lb_pending_import={"id": "41", "status": "waiting", "submitted_at": "T"})
    lb = FakeLB()

    async def status_then_new_upload(token: str, import_id: str) -> dict[str, Any]:
        # The user uploads another export while the poller asks about the old one.
        db.cols["music_services"][doc_id]["lb_pending_import"] = {"id": "42", "status": "waiting", "submitted_at": "U"}
        return {"status": "completed", "progress": "", "metadata": {}}

    lb.get_import_status = status_then_new_upload  # type: ignore[method-assign]
    await _poller(db, lb, FakeIngest()).run(1500)
    assert db.doc("music_services", doc_id)["lb_pending_import"]["id"] == "42"  # type: ignore[index]
    assert db.doc("music_services", doc_id)["lb_pending_import"]["status"] == "waiting"  # type: ignore[index]


@pytest.mark.asyncio
async def test_disconnected_mid_poll_is_not_recreated(db: PollDb) -> None:
    doc_id = _conn(db)
    lb = FakeLB()

    class Disconnecting(FakeIngest):
        async def ingest(self, *args: Any, **kwargs: Any) -> IngestResult:
            db.cols["music_services"].pop(doc_id)
            return await super().ingest(*args, **kwargs)

    lb.add("e2e-fixture-user", 1, T0)
    report = await _poller(db, lb, Disconnecting()).run(1500)
    assert db.doc("music_services", doc_id) is None
    assert report.users_polled == 0


@pytest.mark.asyncio
async def test_lb_errors_skip_user_with_warning(db: PollDb, caplog: pytest.LogCaptureFixture) -> None:
    _conn(db, user="gone", lb_last_polled_at="2026-10-01T00:00:00+00:00")
    _conn(db, user="ok", lb_last_polled_at="2026-10-02T00:00:00+00:00")
    _conn(db, user="nameless", service_username="")
    lb = FakeLB()
    lb.add("e2e-ok", 1, T0)
    lb.errors = [ExternalServiceError("ListenBrainz", "API error: 404")]

    with caplog.at_level("INFO", logger="backend.services.listenbrainz_poller"):
        report = await _poller(db, lb, FakeIngest()).run(1500)

    assert report.users_polled == 1
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    summary = [r for r in caplog.records if r.levelname == "INFO"]
    assert len(summary) == 1 and "users_polled=1" in summary[0].getMessage()


# ---------------------------------------------------------------------------- client


def _client_with(handler: Any) -> ListenBrainzClient:
    client = ListenBrainzClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_client_get_listens_quotes_username_and_raises_rate_limited() -> None:
    seen: list[str] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.raw_path.decode())
        return httpx.Response(200, json={"payload": {"listens": []}})

    await _client_with(ok).get_listens("odd/name?x", count=5000, min_ts=3)
    assert seen[0].startswith("/1/user/odd%2Fname%3Fx/listens?")
    assert "count=1000" in seen[0] and "min_ts=3" in seen[0]

    def limited(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-RateLimit-Reset-In": "4"})

    with pytest.raises(ListenBrainzRateLimitedError) as exc:
        await _client_with(limited).get_listens("e2e-user")
    assert exc.value.retry_after == 4.0


# ---------------------------------------------------------------------------- test API route


def _app(monkeypatch: pytest.MonkeyPatch, *, flag: bool) -> TestClient:
    settings = BackendSettings(environment="development", decide_test_api=flag, google_cloud_project="test-project")
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    return TestClient(main.create_app())


@pytest.fixture
def fake_poller() -> Iterator[MagicMock]:
    poller = MagicMock()
    poller.run = AsyncMock(
        return_value=PollReport(users_polled=2, listens_added=5, imports_checked=1, rate_limited=0, remaining=0)
    )
    yield poller


def test_poll_route_only_with_test_api(monkeypatch: pytest.MonkeyPatch, fake_poller: MagicMock) -> None:
    from backend.api.routes.test_support import get_listenbrainz_poller

    assert _app(monkeypatch, flag=False).post("/api/test/listenbrainz/poll").status_code == 404
    assert _app(monkeypatch, flag=False).post("/internal/listenbrainz/poll").status_code == 404

    c = _app(monkeypatch, flag=True)
    c.app.dependency_overrides[get_listenbrainz_poller] = lambda: fake_poller  # type: ignore[attr-defined]
    resp = c.post("/api/test/listenbrainz/poll")
    assert resp.status_code == 200
    assert resp.json() == {
        "users_polled": 2,
        "listens_added": 5,
        "imports_checked": 1,
        "rate_limited": 0,
        "remaining": 0,
    }
    fake_poller.run.assert_awaited_once_with(1500)


# ---------------------------------------------------------------------------- job entry point


def test_job_entry_runs_poller_with_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.jobs import lb_poll

    seen: list[int] = []

    async def fake_run(settings: BackendSettings) -> None:
        seen.append(settings.lb_poll_budget_seconds)

    monkeypatch.setattr(lb_poll, "_run", fake_run)
    assert lb_poll.main([]) == 0 and seen == [1500]

    async def boom(settings: BackendSettings) -> None:
        raise RuntimeError("firestore down")

    monkeypatch.setattr(lb_poll, "_run", boom)
    assert lb_poll.main([]) == 1
