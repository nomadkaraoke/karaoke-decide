"""GET /api/my/recommendations/v2 through the real app, fixture catalog, mocked Firestore."""

from __future__ import annotations

import base64
import statistics
import time
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalog_store_dep as get_store_dep
from backend.api.deps import get_current_user, get_firestore, get_hidden_keys_provider
from backend.main import app
from backend.services.catalog_store import CatalogUnavailable
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User

URL = "/api/my/recommendations/v2"


class FakeStore:
    """Stands in for CatalogStore: ``require`` returns the fixture reader (or raises)."""

    def __init__(self, reader: CatalogReader | None) -> None:
        self._reader = reader
        self.run = "fixture-run"

    def require(self) -> CatalogReader:
        if self._reader is None:
            raise CatalogUnavailable("warming_up")
        return self._reader


class FakeHidden:
    def __init__(self, keys: set[str] | None = None) -> None:
        self.keys = keys or set()

    async def hidden_keys(self, user_id: str) -> set[str]:
        return set(self.keys)


def _firestore(
    user_doc: dict[str, Any] | None = None,
    user_songs: list[dict[str, Any]] | None = None,
    crowd: dict[str, dict[str, Any]] | Exception | None = None,
) -> MagicMock:
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value=user_doc or {})
    fs.get_many_calls = []

    async def get_many(collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        fs.get_many_calls.append((collection, list(ids)))
        if isinstance(crowd, Exception):
            raise crowd
        return {i: (crowd or {})[i] for i in ids if i in (crowd or {})}

    fs.get_many = AsyncMock(side_effect=get_many)

    async def query(collection: str, **_: Any) -> list[dict[str, Any]]:
        if collection == "decide_users":
            return [user_doc] if user_doc else []
        if collection == "user_songs":
            return user_songs or []
        return []

    fs.query_documents = AsyncMock(side_effect=query)
    return fs


@pytest.fixture
def make_client(reader: CatalogReader) -> Iterator[Callable[..., TestClient]]:
    def make(
        *,
        store: Any = None,
        hidden: set[str] | None = None,
        user_doc: dict[str, Any] | None = None,
        user_id: str = "guest_u1",
        user_songs: list[dict[str, Any]] | None = None,
        crowd: dict[str, dict[str, Any]] | Exception | None = None,
        fs_out: list[MagicMock] | None = None,
    ) -> TestClient:
        fs = _firestore(user_doc, user_songs, crowd)
        if fs_out is not None:
            fs_out.append(fs)
        app.dependency_overrides[get_current_user] = lambda: User(id=user_id, is_guest=True)
        app.dependency_overrides[get_firestore] = lambda: fs
        app.dependency_overrides[get_store_dep] = lambda: store or FakeStore(reader)
        app.dependency_overrides[get_hidden_keys_provider] = lambda: FakeHidden(hidden)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _songs(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for sec in body["sections"] for s in sec["songs"]]


def test_v2_shape(make_client: Callable[..., TestClient]) -> None:
    r = make_client(user_doc={"quiz_genres": ["rock"], "quiz_decades": ["1990s"]}).get(URL)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"sections", "filters_applied", "next_cursor", "has_profile", "catalog_run", "voice"}
    assert body["voice"] == {"has_profile": False, "method": None}
    assert body["catalog_run"] == "fixture-run"
    assert body["has_profile"] is True
    assert body["next_cursor"] is None
    assert body["sections"]
    sec = body["sections"][0]
    assert set(sec) == {"id", "title_key", "reason_key", "title_params", "songs"}
    song = sec["songs"][0]
    assert set(song) == {
        "song_key",
        "artist",
        "title",
        "reason",
        "reason_type",
        "reason_artist",
        "score",
        "has_karaoke_version",
        "karaoke",
        "facets",
        "fit",
        "crowd",
    }
    assert song["fit"] is None  # no voice profile
    assert song["crowd"] is None  # no crowd stats for the song
    assert set(song["karaoke"]) == {"community", "commercial", "karafun", "brand_count"}
    assert set(song["facets"]) == {
        "decade",
        "genres",
        "mood",
        "energy",
        "lead_gender",
        "duration_ms",
        "popularity",
        "is_classic",
        "explicit",
    }
    assert song["reason"]


def test_v2_warming_up_503(make_client: Callable[..., TestClient]) -> None:
    r = make_client(store=FakeStore(None)).get(URL)
    assert r.status_code == 503
    assert r.json() == {"detail": "warming_up"}
    assert r.headers["retry-after"] == "30"


def test_everything_filtered_returns_empty_sections(
    make_client: Callable[..., TestClient], reader: CatalogReader
) -> None:
    ska = [r[0] for r in reader.conn().execute("SELECT song_key FROM song_genres WHERE genre_id = 'ska'")]
    assert ska
    r = make_client(hidden=set(ska)).get(URL, params={"genre": ["ska"]})
    assert r.status_code == 200
    body = r.json()
    assert body["sections"] == []
    assert body["next_cursor"] is None
    assert body["filters_applied"] == {"genre": ["ska"]}
    # flat sort too
    r = make_client(hidden=set(ska)).get(URL, params={"genre": ["ska"], "sort": "az"})
    assert r.status_code == 200 and r.json()["sections"] == []


def test_hidden_song_absent(make_client: Callable[..., TestClient]) -> None:
    client = make_client(user_doc={"quiz_genres": ["rock"]})
    first = _songs(client.get(URL).json())
    assert first
    victim = first[0]["song_key"]
    again = make_client(user_doc={"quiz_genres": ["rock"]}, hidden={victim}).get(URL).json()
    assert victim not in {s["song_key"] for s in _songs(again)}


def test_flat_sort_paging(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    p1 = client.get(URL, params={"sort": "az"}).json()
    assert [s["id"] for s in p1["sections"]] == ["all"]
    first = p1["sections"][0]["songs"]
    assert len(first) == 30
    assert p1["next_cursor"]
    assert base64.urlsafe_b64decode(p1["next_cursor"] + "==") == b"o:30"
    titles = [s["title"].casefold() for s in first]
    assert titles == sorted(titles)

    seen = {s["song_key"] for s in first}
    cursor, pages = p1["next_cursor"], 1
    while cursor:
        body = client.get(URL, params={"sort": "az", "cursor": cursor}).json()
        songs = body["sections"][0]["songs"] if body["sections"] else []
        keys = {s["song_key"] for s in songs}
        assert not keys & seen
        seen |= keys
        cursor, pages = body["next_cursor"], pages + 1
        assert pages < 100
    assert pages >= 2
    last = client.get(URL, params={"sort": "az", "cursor": p1["next_cursor"]}).json()
    assert last["next_cursor"] is None or last["sections"]


@pytest.mark.parametrize(
    "cursor", ["!!!", "bad", base64.urlsafe_b64encode(b"x:3").decode(), base64.urlsafe_b64encode(b"o:-1").decode()]
)
def test_bad_cursor_400(make_client: Callable[..., TestClient], cursor: str) -> None:
    r = make_client().get(URL, params={"sort": "az", "cursor": cursor})
    assert r.status_code == 400
    assert r.json() == {"detail": "bad_cursor"}


def test_oversized_list_params_422(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    for name in ("genre", "mood", "decade"):
        assert client.get(URL, params={name: ["rock"] * 51}).status_code == 422
    assert client.get(URL, params={"mood": "nope"}).status_code == 422
    assert client.get(URL, params={"sort": "nope"}).status_code == 422


def test_filters_echoed(make_client: Callable[..., TestClient]) -> None:
    body = make_client().get(URL, params={"decade": ["1990s"], "explicit": "exclude", "popularity": 10}).json()
    assert body["filters_applied"] == {"decade": ["1990s"], "explicit": "exclude", "popularity": 10}
    assert all(s["facets"]["decade"] == "1990s" and s["facets"]["explicit"] is False for s in _songs(body))


def test_two_quiz_users_differ(make_client: Callable[..., TestClient]) -> None:
    a = make_client(user_doc={"quiz_genres": ["rock"], "quiz_decades": ["1990s"], "quiz_energy_pref": "high"}).get(URL)
    b = make_client(user_doc={"quiz_genres": ["pop"], "quiz_decades": ["2010s"], "quiz_energy_pref": "chill"}).get(URL)
    ka = [s["song_key"] for s in _songs(a.json())][:20]
    kb = [s["song_key"] for s in _songs(b.json())][:20]
    assert ka and kb
    assert len(set(ka) & set(kb)) <= 6


@pytest.mark.slow
def test_p50_under_500ms(make_client: Callable[..., TestClient]) -> None:
    client = make_client(user_doc={"quiz_genres": ["rock"], "quiz_decades": ["1990s"]})
    client.get(URL)  # warm
    times = []
    for _ in range(20):
        t = time.perf_counter()
        assert client.get(URL).status_code == 200
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < 0.5


def test_go_tos_section_and_sung_before_reason(make_client: Callable[..., TestClient], reader: CatalogReader) -> None:
    """Songs sung twice (or rated great) lead the page as go-tos; a latest-bad song isn't known."""
    rows = (
        reader.conn()
        .execute("SELECT song_key, artist, title FROM songs WHERE in_kn = 1 ORDER BY song_key LIMIT 3")
        .fetchall()
    )
    (fave, great, bad) = rows
    log = {"user_id": "guest_u1", "source": "karaoke_log"}
    songs = [
        {**log, "song_id": fave[0], "song_key": fave[0], "artist": fave[1], "title": fave[2], "times_sung": 2},
        {**log, "song_id": great[0], "song_key": great[0], "artist": great[1], "title": great[2], "times_sung": 1,
         "sung_feel_latest": "great"},
        {**log, "song_id": bad[0], "song_key": bad[0], "artist": bad[1], "title": bad[2], "times_sung": 3,
         "sung_feel_latest": "bad", "loved": True},
    ]  # fmt: skip
    body = make_client(user_doc={"quiz_genres": ["rock"]}, user_songs=songs).get(URL).json()
    first = body["sections"][0]
    assert first["id"] == "go_tos"
    assert (first["title_key"], first["reason_key"]) == ("recs.section.go_tos.title", "recs.section.go_tos.reason")
    assert {s["song_key"] for s in first["songs"]} == {fave[0], great[0]}
    assert {(s["reason_type"], s["reason"]) for s in first["songs"]} == {("sung_before", "You've sung this")}
    rest = [s for sec in body["sections"][1:] for s in sec["songs"]]
    assert not {s["song_key"] for s in rest} & {fave[0], great[0]}
    known_shelves = [sec for sec in body["sections"] if sec["id"] in ("go_tos", "songs_you_know")]
    assert bad[0] not in {s["song_key"] for sec in known_shelves for s in sec["songs"]}
    # A latest-bad song is no longer known: it is either a penalised discovery pick or not shown at all.
    elsewhere = [s for s in rest if s["song_key"] == bad[0]]
    assert len(elsewhere) <= 1
    assert all(s["reason_type"] not in ("known_song", "sung_before") for s in elsewhere)


def _stats(right: int, too_high: int = 0, yes: int = 0, answered: int = 0) -> dict[str, Any]:
    return {
        "total": {"right": right, "too_low": 0, "too_high": too_high},
        "by_bucket": {},
        "crowd_pleaser_yes": yes,
        "crowd_pleaser_answered": answered,
    }


def _crowd_section(body: dict[str, Any]) -> list[dict[str, Any]]:
    return next(sec["songs"] for sec in body["sections"] if sec["id"] == "crowd_pleasers")


def test_recs_attach_crowd_and_reorder_crowd_pleasers(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    doc = {"quiz_genres": ["rock"], "quiz_decades": ["1990s"]}
    base = _crowd_section(make_client(user_doc=doc).get(URL).json())
    assert len(base) >= 4
    keys = [s["song_key"] for s in base]
    crowd = {
        keys[-1]: _stats(right=5, yes=4, answered=5),  # 0.8: up front
        keys[-2]: _stats(right=5, yes=3, answered=6),  # 0.5: up front, after keys[-1]'s original slot order
        keys[0]: _stats(right=5, yes=1, answered=5),  # 0.2: stays put
        keys[1]: _stats(right=5, yes=4, answered=4),  # 4 answers: below threshold, stays put
    }
    fs_out: list[MagicMock] = []
    with caplog.at_level("INFO", logger="backend.services.recs_v2.service"):
        body = make_client(user_doc=doc, crowd=crowd, fs_out=fs_out).get(URL).json()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    got = _crowd_section(body)
    assert [s["song_key"] for s in got] == [keys[-2], keys[-1], *keys[:-2]]  # stable among the leaders
    by_key = {s["song_key"]: s for s in got}
    assert by_key[keys[-1]]["crowd"]["crowd_pleaser_share"] == 0.8
    assert by_key[keys[-1]]["crowd"]["singers"] == 5
    assert by_key[keys[1]]["crowd"]["crowd_pleaser_share"] is None
    assert all(s["crowd"] is None for s in _songs(body) if s["song_key"] not in crowd)
    # one batch read for every song on the page
    calls = fs_out[0].get_many_calls
    assert len(calls) == 1 and calls[0][0] == "song_crowd_stats"
    assert set(calls[0][1]) == {s["song_key"] for s in _songs(body)}
    # other sections keep their order
    others = [sec for sec in body["sections"] if sec["id"] != "crowd_pleasers"]
    base_body = make_client(user_doc=doc).get(URL).json()
    assert [[s["song_key"] for s in sec["songs"]] for sec in others] == [
        [s["song_key"] for s in sec["songs"]] for sec in base_body["sections"] if sec["id"] != "crowd_pleasers"
    ]


def test_recs_crowd_read_failure_degrades(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    doc = {"quiz_genres": ["rock"]}
    with caplog.at_level("INFO"):
        r = make_client(user_doc=doc, crowd=RuntimeError("firestore down")).get(URL)
    assert r.status_code == 200
    assert all(s["crowd"] is None for s in _songs(r.json()))
    levels = {rec.levelname for rec in caplog.records if "crowd" in rec.getMessage()}
    assert levels == {"WARNING"}


def test_recs_slow_crowd_read_logs_info(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services.recs_v2 import service as svc

    ticks = iter([0.0, 0.2])
    monkeypatch.setattr(svc, "_now", lambda: next(ticks))
    with caplog.at_level("INFO", logger="backend.services.recs_v2.service"):
        assert make_client(user_doc={"quiz_genres": ["rock"]}).get(URL).status_code == 200
    slow = [rec for rec in caplog.records if "crowd" in rec.getMessage()]
    assert [rec.levelname for rec in slow] == ["INFO"]
