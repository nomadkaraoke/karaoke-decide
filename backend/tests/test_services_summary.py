"""GET /api/services/summary, the list's new fields, and DELETE /api/services/sources/{source}/data."""

from __future__ import annotations

import copy
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_firestore, get_settings
from backend.config import BackendSettings
from backend.services.music_service_service import MusicServiceService
from karaoke_decide.core.models import User

USER = "fixture-user"


class FakeDb:
    """In-memory Firestore: equality-filter queries, aggregation counts, deletes."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    def put(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols.setdefault(collection, {})[doc_id] = data

    def _match(self, collection: str, filters: Any) -> list[dict[str, Any]]:
        docs = [{"id": i, **copy.deepcopy(d)} for i, d in self.cols.get(collection, {}).items()]
        for field, _op, value in filters or []:
            docs = [d for d in docs if d.get(field) == value]
        return docs

    async def query_documents(self, collection: str, filters: Any = None, **_: Any) -> list[dict[str, Any]]:
        return self._match(collection, filters)

    async def count_documents(self, collection: str, filters: Any = None) -> int:
        return len(self._match(collection, filters))

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.cols.get(collection, {}).get(doc_id)
        return {"id": doc_id, **copy.deepcopy(doc)} if doc is not None else None

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self.cols.get(collection, {}).pop(doc_id, None)

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        for i in doc_ids:
            await self.delete_document(collection, i)

    async def batch_set(self, collection: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        for i, d in docs.items():
            self.cols.setdefault(collection, {}).setdefault(i, {}).update(d)


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def client(db: FakeDb) -> Generator[TestClient, None, None]:
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id=USER)
    app.dependency_overrides[get_firestore] = lambda: db
    app.dependency_overrides[get_settings] = lambda: BackendSettings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _conn(db: FakeDb, service_type: str, **extra: Any) -> None:
    db.put(
        "music_services",
        f"{USER}_{service_type}",
        {
            "user_id": USER,
            "service_type": service_type,
            "service_user_id": "u",
            "service_username": "e2e-listener",
            "sync_status": "idle",
            **extra,
        },
    )


def _rows(db: FakeDb, source: str, songs: int, artists: int = 0, other_user: bool = False) -> None:
    uid = "someone-else" if other_user else USER
    for n in range(songs):
        db.put("user_songs", f"{uid}:{source}:s{n}", {"user_id": uid, "source": source, "sources": [source]})
    for n in range(artists):
        db.put("user_artists", f"{uid}:{source}:a{n}", {"user_id": uid, "source": source})


def test_summary_merges_connections_and_imports(client: TestClient, db: FakeDb) -> None:
    _conn(db, "lastfm", last_sync_at="2026-10-01T00:00:00+00:00")
    _conn(db, "apple_music")
    _rows(db, "lastfm", songs=3, artists=2)
    _rows(db, "lastfm", songs=5, other_user=True)
    _rows(db, "screenshot", songs=4, artists=1)
    db.put("user_imports", f"{USER}:screenshot", {"user_id": USER, "source": "screenshot", "last_import_at": "T1"})
    db.put("user_imports", f"{USER}:lastfm", {"user_id": USER, "source": "lastfm", "last_import_at": "T2"})
    db.put("user_imports", "other:youtube_music", {"user_id": "other", "source": "youtube_music"})

    body = client.get("/api/services/summary").json()
    by = {r["source"]: r for r in body["sources"]}
    assert set(by) == {"lastfm", "apple_music", "screenshot"}
    assert by["lastfm"] == {
        "source": "lastfm",
        "connected": True,
        "username": "e2e-listener",
        "artists": 2,
        "songs": 3,
        "last_import_at": "T2",
        "status": "ok",
        "auth": "username",
        "pending_listenbrainz_import": None,
    }
    assert by["apple_music"]["auth"] == "oauth" and by["apple_music"]["songs"] == 0
    assert by["screenshot"]["connected"] is False and by["screenshot"]["auth"] is None
    assert by["screenshot"]["songs"] == 4 and by["screenshot"]["last_import_at"] == "T1"
    assert body["capabilities"]["listenbrainz_oauth"] is False


def test_summary_reads_lb_pending_import(client: TestClient, db: FakeDb) -> None:
    _conn(db, "listenbrainz", lb_pending_import={"id": "42", "status": "waiting", "submitted_at": "T"}, auth="oauth")
    row = client.get("/api/services/summary").json()["sources"][0]
    assert row["pending_listenbrainz_import"] == {"id": "42", "status": "waiting"}


def test_summary_pending_listenbrainz_import(client: TestClient, db: FakeDb) -> None:
    _conn(db, "listenbrainz", pending_listenbrainz_import={"id": "imp-1", "status": "queued"}, auth="oauth")
    row = client.get("/api/services/summary").json()["sources"][0]
    assert row["pending_listenbrainz_import"] == {"id": "imp-1", "status": "queued"}
    assert row["auth"] == "oauth"


@pytest.mark.parametrize(
    ("conn", "imp", "expected"),
    [
        ({"sync_status": "syncing", "error_code": "needs_reconnect"}, {}, "importing"),
        ({}, {"status": "importing", "lb_stale": True}, "importing"),
        ({"error_code": "needs_reconnect", "lb_stale": True, "sync_status": "error"}, {}, "needs_reconnect"),
        ({"lb_stale": True, "sync_status": "error"}, {}, "stale"),
        ({"sync_status": "error"}, {}, "error"),
        ({"sync_status": "idle"}, {"status": "ok"}, "ok"),
    ],
)
def test_status_mapping_table(conn: dict[str, Any], imp: dict[str, Any], expected: str) -> None:
    row = MusicServiceService._summary_row("listenbrainz", {"service_username": "x", **conn}, imp, 0, 0)
    assert row["status"] == expected


def test_list_services_has_new_fields(client: TestClient, db: FakeDb) -> None:
    _conn(db, "lastfm", sync_status="error", sync_error="boom", tracks_synced=1, songs_synced=2)
    _rows(db, "lastfm", songs=3, artists=2)
    db.put("user_imports", f"{USER}:lastfm", {"user_id": USER, "source": "lastfm", "last_import_at": "T2"})
    [svc] = client.get("/api/services").json()
    assert svc["service_type"] == "lastfm" and svc["tracks_synced"] == 1  # existing shape intact
    assert (svc["artists"], svc["songs"], svc["last_import_at"], svc["status"]) == (2, 3, "T2", "error")


def test_remove_data_unknown_source_400(client: TestClient) -> None:
    assert client.delete("/api/services/sources/napster/data").status_code == 400


def test_remove_data_deletes_connection(client: TestClient, db: FakeDb) -> None:
    _conn(db, "apple_music")
    _rows(db, "apple_music", songs=2, artists=1)
    _rows(db, "lastfm", songs=1)
    resp = client.delete("/api/services/sources/apple_music/data")
    assert resp.status_code == 200
    assert resp.json() == {"removed_songs": 2, "removed_artists": 1}
    assert db.cols["music_services"] == {}
    assert len(db.cols["user_songs"]) == 1  # lastfm row untouched


def test_remove_data_without_connection_and_export_sums(client: TestClient, db: FakeDb) -> None:
    _rows(db, "spotify_export", songs=1)
    db.put("user_import_artists", f"{USER}:spotify_export:k1", {"user_id": USER, "source": "spotify_export"})
    db.put("user_import_artists", f"{USER}:apple_export:k2", {"user_id": USER, "source": "apple_export"})
    db.put("user_imports", f"{USER}:spotify_export", {"user_id": USER, "source": "spotify_export"})
    resp = client.delete("/api/services/sources/spotify_export/data")
    assert resp.status_code == 200 and resp.json()["removed_songs"] == 1
    assert list(db.cols["user_import_artists"]) == [f"{USER}:apple_export:k2"]
    assert db.cols["user_imports"] == {}


def test_list_services_survives_summary_lookup_failure(
    client: TestClient, db: FakeDb, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """decide web depends on this route: a failing count lookup returns zeros / None, never 500."""
    _conn(db, "lastfm", tracks_synced=1, songs_synced=2)
    _rows(db, "lastfm", songs=3, artists=2)

    async def boom(*_: Any, **__: Any) -> int:
        raise RuntimeError("aggregation query failed")

    monkeypatch.setattr(db, "count_documents", boom)
    with caplog.at_level("WARNING", logger="backend.api.routes.services"):
        resp = client.get("/api/services")
    assert resp.status_code == 200
    [svc] = resp.json()
    assert svc["service_type"] == "lastfm" and svc["tracks_synced"] == 1
    assert (svc["artists"], svc["songs"], svc["last_import_at"], svc["status"]) == (0, 0, None, "ok")
    assert any(r.levelname == "WARNING" and "summary lookup failed" in r.getMessage() for r in caplog.records)


def test_list_services_queries_connections_once(client: TestClient, db: FakeDb) -> None:
    _conn(db, "lastfm")
    calls: list[str] = []
    real = db.query_documents

    async def spy(collection: str, filters: Any = None, **kw: Any) -> list[dict[str, Any]]:
        calls.append(collection)
        return await real(collection, filters, **kw)

    db.query_documents = spy  # type: ignore[method-assign]
    assert client.get("/api/services").status_code == 200
    assert calls.count("music_services") == 1


def test_guest_cannot_extract_screenshot(client: TestClient) -> None:
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id="e2e-guest", is_guest=True)
    resp = client.post("/api/services/screenshot/extract", files=[("images", ("s.png", b"\x89PNG fake", "image/png"))])
    assert resp.status_code == 403


def test_guest_cannot_import_spotify_playlist(client: TestClient) -> None:
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: User(id="e2e-guest", is_guest=True)
    resp = client.post(
        "/api/services/spotify-playlist/import",
        json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"},
    )
    assert resp.status_code == 403


def test_summary_known_songs_deduped(client: TestClient, db: FakeDb) -> None:
    def song(doc_id: str, **f: Any) -> None:
        db.put("user_songs", doc_id, {"user_id": USER, **f})

    song("a", song_key="k1", source="lastfm", sources=["lastfm"], playcount=20)
    song("b", song_key="k1", source="spotify", sources=["spotify"])  # same song again: once
    song("c", song_key="k2", source="voice_session", sources=["voice_session"])  # session-only: 0
    song("d", song_key="k3", source="voice_session", sources=["voice_session"], loved=True)  # loved: counts
    song("e", song_key="k4", source="screenshot", sources=["screenshot"])
    db.put("user_songs", "z", {"user_id": "other", "song_key": "k9", "source": "lastfm"})
    assert client.get("/api/services/summary").json()["known_songs"] == 3


def test_summary_known_songs_keyless_rows_use_library_identity(client: TestClient, db: FakeDb) -> None:
    for doc_id, source in (("a", "lastfm"), ("b", "screenshot")):
        db.put("user_songs", doc_id, {"user_id": USER, "artist": "Some Band", "title": "Some Song", "source": source})
    assert client.get("/api/services/summary").json()["known_songs"] == 1
