"""Not for me / Love it feedback: service and routes (in-memory Firestore, no GCP)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalog_store_dep, get_current_user, get_firestore
from backend.main import app
from backend.services.catalog_store import CatalogUnavailable
from backend.services.song_feedback_service import (
    HIDDEN_COLLECTION,
    SongFeedbackService,
    feedback_doc_id,
    loved_doc_id,
)
from karaoke_decide.core.models import User

KEYS = [
    "ac dc|back in black",
    "a&b|c#?",
    "x/y|z%20",
    "beyoncé|halo",
    "فيروز|يا أنا يا أنا",
]


class MemoryFirestore:
    """Just the FirestoreService calls the feedback service uses."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        d = self.cols.get(collection, {}).get(doc_id)
        return None if d is None else {"id": doc_id, **d}

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        col = self.cols.setdefault(collection, {})
        if merge and doc_id in col:
            col[doc_id] = {**col[doc_id], **data}
        else:
            col[doc_id] = dict(data)

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self.cols.get(collection, {}).pop(doc_id, None)

    async def run_transaction(self, fn: Any) -> Any:
        """Reads see committed state; writes apply at commit (Firestore's model)."""
        self.transactions = getattr(self, "transactions", 0) + 1
        writes: list[tuple[str, str, dict[str, Any] | None]] = []
        cols = self.cols

        class Txn:
            async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
                assert not writes, "read after write inside a transaction"
                d = cols.get(collection, {}).get(doc_id)
                return None if d is None else dict(d)

            def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
                writes.append((collection, doc_id, dict(data)))

            def delete(self, collection: str, doc_id: str) -> None:
                writes.append((collection, doc_id, None))

        result = await fn(Txn())
        for collection, doc_id, data in writes:
            if data is None:
                cols.get(collection, {}).pop(doc_id, None)
            else:
                cols.setdefault(collection, {})[doc_id] = data
        return result

    async def query_documents(self, collection: str, filters: Any = None, **_: Any) -> list[dict[str, Any]]:
        out = []
        for doc_id, d in self.cols.get(collection, {}).items():
            if all(d.get(f) == v for f, _op, v in filters or []):
                out.append({"id": doc_id, **d})
        return out


class FakeReader:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE songs (song_key TEXT PRIMARY KEY, artist TEXT, title TEXT)")
        self._conn.execute("INSERT INTO songs VALUES ('adele|hello', 'Adele', 'Hello')")

    def conn(self) -> sqlite3.Connection:
        return self._conn


class FakeStore:
    def __init__(self, reader: FakeReader | None) -> None:
        self._reader = reader

    def require(self) -> FakeReader:
        if self._reader is None:
            raise CatalogUnavailable("warming_up")
        return self._reader


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.fixture
def client(fs: MemoryFirestore) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_user] = lambda: User(id="u1", is_guest=True)
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_catalog_store_dep] = lambda: FakeStore(FakeReader())
    yield TestClient(app)
    app.dependency_overrides.clear()


def _svc(fs: MemoryFirestore, kn_ids: dict[str, int] | None = None) -> SongFeedbackService:
    kn = kn_ids or {}
    return SongFeedbackService(fs, kn_id_for=kn.get)  # type: ignore[arg-type]


def test_doc_id_has_no_slash() -> None:
    for key in KEYS + ["a/b/c", "//"]:
        doc_id = feedback_doc_id("guest_u1", key)
        assert "/" not in doc_id
        assert doc_id.startswith("guest_u1:")
    assert feedback_doc_id("u", "k") == feedback_doc_id("u", "k")
    assert feedback_doc_id("u", "k") != feedback_doc_id("u", "k2")


@pytest.mark.asyncio
async def test_hide_then_unhide(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.hide("u1", "adele|hello", "Adele", "Hello")
    assert await svc.hidden_keys("u1") == {"adele|hello"}
    assert await svc.hidden_keys("u2") == set()
    listed = await svc.list_hidden("u1")
    assert [(s.song_key, s.artist, s.title) for s in listed] == [("adele|hello", "Adele", "Hello")]
    await svc.unhide("u1", "adele|hello")
    assert await svc.hidden_keys("u1") == set()
    assert fs.cols[HIDDEN_COLLECTION] == {}


@pytest.mark.asyncio
async def test_love_adds_user_song_manual(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    ((doc_id, doc),) = fs.cols["user_songs"].items()
    assert doc_id.startswith("u1:sk:") and "/" not in doc_id
    assert doc["source"] == "manual"
    assert doc["song_id"] == doc["song_key"] == "adele|hello"
    assert doc["user_id"] == "u1"
    assert (doc["artist"], doc["title"], doc["play_count"]) == ("Adele", "Hello", 0)
    assert [s.song_key for s in await svc.list_loved("u1")] == ["adele|hello"]
    await svc.unlove("u1", "adele|hello")
    assert fs.cols["user_songs"] == {}


@pytest.mark.asyncio
async def test_list_loved_ignores_other_sources(fs: MemoryFirestore) -> None:
    await fs.set_document("user_songs", "u1:123", {"user_id": "u1", "source": "spotify", "song_id": "123"})
    assert await _svc(fs).list_loved("u1") == []


@pytest.mark.asyncio
async def test_love_merges_into_a_row_with_sung_history(fs: MemoryFirestore) -> None:
    """A row the karaoke log created keeps times_sung, last_sung_at and its source when loved."""
    row_id = loved_doc_id("u1", "adele|hello")
    fs.cols["user_songs"] = {
        row_id: {
            "user_id": "u1",
            "song_id": "adele|hello",
            "song_key": "adele|hello",
            "source": "karaoke_log",
            "artist": "Adele",
            "title": "Hello",
            "times_sung": 2,
            "last_sung_at": "2026-10-20T22:00:00+00:00",
            "created_at": "2026-10-20T22:00:00+00:00",
        }
    }
    svc = _svc(fs)
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    row = fs.cols["user_songs"][row_id]
    assert (row["times_sung"], row["last_sung_at"], row["source"]) == (2, "2026-10-20T22:00:00+00:00", "karaoke_log")
    assert row["loved"] is True and isinstance(row["loved_at"], str)
    assert row["created_at"] == "2026-10-20T22:00:00+00:00"
    assert [s.song_key for s in await svc.list_loved("u1")] == ["adele|hello"]

    await svc.unlove("u1", "adele|hello")
    row = fs.cols["user_songs"][row_id]  # sung history: kept, love marker cleared
    assert row["times_sung"] == 2 and row["loved"] is False
    assert await svc.list_loved("u1") == []


@pytest.mark.asyncio
async def test_love_uses_existing_kn_id_row(fs: MemoryFirestore) -> None:
    """Same row rule as the karaoke log: the {user}:{kn_id} row when catalog.db maps the key and it exists."""
    fs.cols["user_songs"] = {"u1:77": {"user_id": "u1", "song_id": "77", "source": "spotify", "artist": "Adele"}}
    svc = _svc(fs, {"adele|hello": 77})
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    assert list(fs.cols["user_songs"]) == ["u1:77"]
    row = fs.cols["user_songs"]["u1:77"]
    assert (row["source"], row["song_id"], row["song_key"], row["title"]) == ("spotify", "77", "adele|hello", "Hello")
    assert [s.song_key for s in await svc.list_loved("u1")] == ["adele|hello"]
    await svc.unlove("u1", "adele|hello")
    assert fs.cols["user_songs"]["u1:77"]["loved"] is False  # a synced row is never deleted
    assert await svc.list_loved("u1") == []


@pytest.mark.asyncio
async def test_love_falls_back_to_song_key_row_when_kn_row_missing(fs: MemoryFirestore) -> None:
    svc = _svc(fs, {"adele|hello": 77})
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    assert list(fs.cols["user_songs"]) == [loved_doc_id("u1", "adele|hello")]
    assert fs.cols["user_songs"][loved_doc_id("u1", "adele|hello")]["source"] == "manual"


@pytest.mark.asyncio
async def test_unlove_keeps_quiz_row(fs: MemoryFirestore) -> None:
    row_id = loved_doc_id("u1", "adele|hello")
    fs.cols["user_songs"] = {row_id: {"user_id": "u1", "source": "quiz", "song_key": "adele|hello"}}
    svc = _svc(fs)
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    await svc.unlove("u1", "adele|hello")
    assert fs.cols["user_songs"][row_id]["source"] == "quiz"


@pytest.mark.asyncio
async def test_love_and_unlove_run_in_a_transaction(fs: MemoryFirestore) -> None:
    """The row is re-read inside the transaction, so a log increment between love's reads
    and its write can't be lost (Firestore retries the transaction on contention)."""
    svc = _svc(fs)
    await svc.love("u1", "adele|hello", "Adele", "Hello")
    await svc.unlove("u1", "adele|hello")
    assert getattr(fs, "transactions", 0) == 2


@pytest.mark.asyncio
async def test_love_keeps_a_concurrent_log_increment(fs: MemoryFirestore) -> None:
    """A log increment that lands while love is running (here: during the kn_id lookup, before
    any row read) is kept, and its source isn't overwritten: love reads the row in its transaction."""
    row_id = loved_doc_id("u1", "adele|hello")

    def racing_kn_lookup(song_key: str) -> int | None:
        fs.cols.setdefault("user_songs", {})[row_id] = {
            "user_id": "u1",
            "song_key": song_key,
            "source": "karaoke_log",
            "times_sung": 1,
        }
        return None

    await SongFeedbackService(fs, kn_id_for=racing_kn_lookup).love("u1", "adele|hello", "Adele", "Hello")  # type: ignore[arg-type]
    row = fs.cols["user_songs"][row_id]
    assert (row["times_sung"], row["source"], row["loved"]) == (1, "karaoke_log", True)


@pytest.mark.asyncio
async def test_list_loved_dedupes_on_song_key(fs: MemoryFirestore) -> None:
    fs.cols["user_songs"] = {
        loved_doc_id("u1", "adele|hello"): {
            "user_id": "u1",
            "song_key": "adele|hello",
            "source": "manual",
            "artist": "Adele",
            "title": "Hello",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        "u1:77": {
            "user_id": "u1",
            "song_key": "adele|hello",
            "source": "spotify",
            "loved": True,
            "artist": "Adele",
            "title": "Hello",
            "created_at": "2026-02-01T00:00:00+00:00",
        },
    }
    assert [s.song_key for s in await _svc(fs).list_loved("u1")] == ["adele|hello"]


def test_loved_rows_weigh_as_manual_in_recs() -> None:
    from backend.services.recs_v2 import weights as w

    assert w.song_weight({"source": "spotify", "loved": True}) == w.SONG_MANUAL
    assert w.song_weight({"source": "karaoke_log", "loved": True}) == w.SONG_MANUAL
    assert w.song_weight({"source": "manual"}) == w.SONG_MANUAL  # legacy loved rows carry no marker
    assert w.song_weight({"source": "manual", "loved": False, "times_sung": 1}) == w.SONG_DEFAULT


@pytest.mark.parametrize("key", KEYS)
def test_song_key_round_trip(client: TestClient, fs: MemoryFirestore, key: str) -> None:
    enc = quote(key, safe="")
    r = client.post(f"/api/my/songs/{enc}/hide", json={"artist": "A", "title": "T"})
    assert r.status_code == 204
    r = client.post(f"/api/my/songs/{enc}/love", json={"artist": "A", "title": "T"})
    assert r.status_code == 204
    assert {d["song_key"] for d in fs.cols[HIDDEN_COLLECTION].values()} == {key}
    assert {d["song_key"] for d in fs.cols["user_songs"].values()} == {key}
    assert all("/" not in i for i in list(fs.cols[HIDDEN_COLLECTION]) + list(fs.cols["user_songs"]))
    assert [s["song_key"] for s in client.get("/api/my/songs/hidden").json()["songs"]] == [key]
    assert [s["song_key"] for s in client.get("/api/my/songs/loved").json()["songs"]] == [key]
    assert client.delete(f"/api/my/songs/{enc}/hide").status_code == 204
    assert client.delete(f"/api/my/songs/{enc}/love").status_code == 204
    assert fs.cols[HIDDEN_COLLECTION] == {} and fs.cols["user_songs"] == {}


def test_unencoded_slash_key_also_works(client: TestClient, fs: MemoryFirestore) -> None:
    assert client.post("/api/my/songs/x/y|z/hide", json={"artist": "A", "title": "T"}).status_code == 204
    assert {d["song_key"] for d in fs.cols[HIDDEN_COLLECTION].values()} == {"x/y|z"}


def test_missing_body_looks_up_catalog(client: TestClient, fs: MemoryFirestore) -> None:
    assert client.post("/api/my/songs/adele%7Chello/hide").status_code == 204
    (doc,) = fs.cols[HIDDEN_COLLECTION].values()
    assert (doc["artist"], doc["title"]) == ("Adele", "Hello")


def test_unknown_song_404(client: TestClient) -> None:
    r = client.post("/api/my/songs/nobody%7Cnothing/love")
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown_song"


def test_catalog_warming_up_503_only_when_lookup_needed(fs: MemoryFirestore) -> None:
    app.dependency_overrides[get_current_user] = lambda: User(id="u1", is_guest=True)
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_catalog_store_dep] = lambda: FakeStore(None)
    try:
        c = TestClient(app)
        assert c.post("/api/my/songs/adele%7Chello/hide").status_code == 503
        assert c.post("/api/my/songs/adele%7Chello/hide", json={"artist": "A", "title": "T"}).status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_get_songs_still_existing_handler(recommendations_client: TestClient) -> None:
    r = recommendations_client.get("/api/my/songs", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert {"songs", "total", "page", "per_page", "has_more"} <= set(r.json())


def test_hidden_keys_provider_is_feedback_service() -> None:
    import asyncio

    from backend.api.deps import get_hidden_keys_provider

    fs = MemoryFirestore()
    app.dependency_overrides[get_firestore] = lambda: fs
    try:
        provider = asyncio.run(get_hidden_keys_provider(SongFeedbackService(fs)))  # type: ignore[arg-type]
    finally:
        app.dependency_overrides.clear()
    assert isinstance(provider, SongFeedbackService)


@pytest.mark.asyncio
async def test_love_then_get_user_songs_does_not_crash(fs: MemoryFirestore) -> None:
    """Regression: love() wrote datetimes; get_user_songs fromisoformat()'d them -> 500."""
    from unittest.mock import MagicMock

    from backend.services.recommendation_service import RecommendationService

    await _svc(fs).love("u1", "adele|hello", "Adele", "Hello")
    doc = next(iter(fs.cols["user_songs"].values()))
    assert isinstance(doc["created_at"], str)
    assert isinstance(doc["updated_at"], str)

    async def count_documents(collection: str, filters: Any = None, **_: Any) -> int:
        return len(await fs.query_documents(collection, filters=filters))

    fs.count_documents = count_documents  # type: ignore[attr-defined]
    rec = RecommendationService(settings=MagicMock(), firestore=fs, bigquery_client=MagicMock())  # type: ignore[arg-type]
    songs, total = await rec.get_user_songs("u1")
    assert total == 1
    assert songs[0].title == "Hello"


@pytest.mark.asyncio
async def test_list_tolerates_legacy_datetime_and_iso_created_at(fs: MemoryFirestore) -> None:
    from datetime import UTC, datetime

    fs.cols["user_hidden_songs"] = {
        "a": {
            "user_id": "u1",
            "song_key": "a|a",
            "artist": "A",
            "title": "A",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        "b": {
            "user_id": "u1",
            "song_key": "b|b",
            "artist": "B",
            "title": "B",
            "created_at": "2026-02-01T00:00:00+00:00",
        },
    }
    songs = await _svc(fs).list_hidden("u1")
    assert [s.song_key for s in songs] == ["b|b", "a|a"]
