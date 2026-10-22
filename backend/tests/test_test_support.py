"""/api/test/* local-stack support routes: mounted only with DECIDE_TEST_API, never in production."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api.deps import get_catalog_store_dep, get_current_user, get_firestore
from backend.config import BackendSettings
from backend.tests.recs_v2.conftest import catalog_dir, reader  # noqa: F401  (fixtures)
from backend.tests.recs_v2.test_personas import _rock_artists
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User

EMAIL = "Maestro@Example.com"
EMAIL_HASH = hashlib.sha256(EMAIL.lower().encode()).hexdigest()


class FakeFirestore:
    """In-memory Firestore covering the calls the test API and recs v2 make."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_document(self, col: str, doc_id: str) -> dict[str, Any] | None:
        d = self.cols.get(col, {}).get(doc_id)
        return None if d is None else {"id": doc_id, **d}

    async def set_document(self, col: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        if merge:
            self.cols.setdefault(col, {}).setdefault(doc_id, {}).update(data)
        else:
            self.cols.setdefault(col, {})[doc_id] = dict(data)

    async def create_document(self, col: str, doc_id: str, data: dict[str, Any]) -> bool:
        if doc_id in self.cols.get(col, {}):
            return False
        self.cols.setdefault(col, {})[doc_id] = dict(data)
        return True

    async def update_document(self, col: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols[col][doc_id].update(data)

    async def delete_document(self, col: str, doc_id: str) -> None:
        self.cols.get(col, {}).pop(doc_id, None)

    async def batch_set(self, col: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        for doc_id, data in docs.items():
            await self.set_document(col, doc_id, data, merge=merge)

    async def batch_delete(self, col: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            await self.delete_document(col, doc_id)

    async def query_documents(
        self,
        col: str,
        filters: list[tuple[str, str, Any]] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        rows = [
            {"id": i, **d}
            for i, d in self.cols.get(col, {}).items()
            if all(d.get(f) == v for f, _op, v in filters or [])
        ]
        rows = rows[offset or 0 :]
        return rows[:limit] if limit else rows


def _app_with(monkeypatch: pytest.MonkeyPatch, *, flag: bool, environment: str) -> TestClient:
    settings = BackendSettings(environment=environment, decide_test_api=flag, google_cloud_project="test-project")  # type: ignore[arg-type]
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    return TestClient(main.create_app())


@pytest.fixture
def fs() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore) -> Iterator[TestClient]:
    c = _app_with(monkeypatch, flag=True, environment="development")
    c.app.dependency_overrides[get_firestore] = lambda: fs  # type: ignore[attr-defined]
    yield c
    c.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_not_mounted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app_with(monkeypatch, flag=False, environment="development")
    assert c.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 404
    assert c.get("/api/test/profile", params={"email": EMAIL}).status_code == 404


def test_not_mounted_in_production_even_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app_with(monkeypatch, flag=True, environment="production")
    assert c.post("/api/test/profile/seed", json={"email": EMAIL}).status_code == 404
    assert c.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 404
    assert c.get("/api/test/profile", params={"email": EMAIL}).status_code == 404


def test_flag_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_TEST_API", "1")
    assert BackendSettings().decide_test_api is True


SEED = {
    "email": EMAIL,
    "genres": ["rock", "pop"],
    "decades": ["1990s"],
    "energy": "high",
    "artists": [{"name": "Invented Band", "affinity": "love"}, {"name": "Other Act", "affinity": "like"}],
    "loved": ["invented band|first song"],
    "hidden": ["other act|second song"],
}


def test_seed_then_read(client: TestClient, fs: FakeFirestore) -> None:
    r = client.post("/api/test/profile/seed", json=SEED)
    assert r.status_code == 200
    user_id = r.json()["user_id"]
    assert user_id.startswith("user_")
    assert fs.cols["decide_users"][EMAIL_HASH]["user_id"] == user_id

    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body == {
        "user_id": user_id,
        "quiz_completed": True,
        "genres": ["rock", "pop"],
        "decades": ["1990s"],
        "energy": "high",
        "artists": [{"name": "Invented Band", "affinity": "love"}, {"name": "Other Act", "affinity": "like"}],
        "loved": ["invented band|first song"],
        "hidden": ["other act|second song"],
    }


def test_seed_is_idempotent_and_replaces(client: TestClient) -> None:
    first = client.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    second = client.post("/api/test/profile/seed", json={"email": EMAIL, "quiz_completed": False}).json()["user_id"]
    assert first == second
    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body["quiz_completed"] is False
    assert (body["genres"], body["artists"], body["loved"], body["hidden"], body["energy"]) == ([], [], [], [], None)


def test_read_unknown_email_is_empty_profile(client: TestClient) -> None:
    body = client.get("/api/test/profile", params={"email": "nobody@example.com"}).json()
    assert body["user_id"] is None
    assert body["quiz_completed"] is False
    assert body["artists"] == []


def test_reset_clears(client: TestClient, fs: FakeFirestore) -> None:
    user_id = client.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    fs.cols.setdefault("user_artists", {})[f"{user_id}:x"] = {"user_id": user_id, "artist_name": "X"}
    fs.cols.setdefault("user_songs", {})[f"{user_id}:s1"] = {"user_id": user_id, "source": "quiz"}
    fs.cols.setdefault("quiz_progress", {})[user_id] = {"user_id": user_id}
    fs.cols["user_songs"]["someone_else:s1"] = {"user_id": "someone_else"}

    assert client.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 204

    assert EMAIL_HASH not in fs.cols["decide_users"]
    assert not fs.cols.get("user_artists")
    assert not fs.cols.get("user_hidden_songs")
    assert not fs.cols.get("quiz_progress")
    assert list(fs.cols["user_songs"]) == ["someone_else:s1"]
    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body["user_id"] is None


def test_reset_unknown_email_is_noop(client: TestClient) -> None:
    assert client.post("/api/test/profile/reset", json={"email": "nobody@example.com"}).status_code == 204


def test_seeded_profile_drives_v2(client: TestClient, fs: FakeFirestore, reader: CatalogReader) -> None:  # noqa: F811
    class Store:
        run = "fixture-run"

        def require(self) -> CatalogReader:
            return reader

    artists = _rock_artists(reader, 2)
    seed = {
        "email": EMAIL,
        "genres": ["rock"],
        "decades": ["1990s"],
        "energy": "high",
        "artists": [{"name": a.name, "affinity": "love"} for a in artists.values()],
    }
    user_id = client.post("/api/test/profile/seed", json=seed).json()["user_id"]
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: User(id=user_id)  # type: ignore[attr-defined]
    app.dependency_overrides[get_catalog_store_dep] = lambda: Store()  # type: ignore[attr-defined]

    r = client.get("/api/my/recommendations/v2")
    assert r.status_code == 200
    body = r.json()
    assert body["has_profile"] is True
    assert "artists_you_love" in [s["id"] for s in body["sections"]]


def test_read_karaoke_log(client: TestClient, fs: FakeFirestore) -> None:
    user_id = client.post("/api/test/profile/seed", json={"email": EMAIL}).json()["user_id"]
    log = fs.cols.setdefault("karaoke_log", {})
    log[f"{user_id}:kj_1"] = {
        "id": "kj_1",
        "user_id": user_id,
        "email_hash": EMAIL_HASH,
        "source": "kjbox",
        "state": "sung",
        "artist": "Invented Band",
        "title": "First Song",
        "song_key": "invented band|first song",
        "song_key_status": "resolved",
        "updated_at": "2026-10-21T21:00:00+00:00",
        "survey": {"feel": "great"},
        "counted": {"collection": "user_songs", "doc_id": "x"},
    }
    log[f"{user_id}:app_2"] = {
        "id": "app_2",
        "user_id": user_id,
        "source": "app",
        "state": "sung",
        "artist": "Other Act",
        "title": "Second Song",
        "song_key_status": "unresolved",
        "deleted": True,
        "updated_at": "2026-10-21T22:00:00+00:00",
    }
    log["someone_else:kj_1"] = {"id": "kj_1", "user_id": "someone_else", "source": "kjbox", "state": "sung"}
    fs.cols.setdefault("user_songs", {})[f"{user_id}:sk:1"] = {
        "user_id": user_id,
        "song_key": "invented band|first song",
        "artist": "Invented Band",
        "title": "First Song",
        "source": "karaoke_log",
        "times_sung": 1,
        "last_sung_at": "2026-10-21T21:00:00+00:00",
    }
    fs.cols.setdefault("user_karaoke_unresolved", {})[f"{user_id}:u1"] = {
        "user_id": user_id,
        "artist": "Other Act",
        "title": "Second Song",
        "times_sung": 1,
    }

    body = client.get("/api/test/karaoke-log", params={"email": EMAIL.upper()}).json()
    assert body["user_id"] == user_id
    assert [(e["id"], e["deleted"]) for e in body["entries"]] == [("app_2", True), ("kj_1", False)]
    assert body["entries"][1]["survey"]["feel"] == "great"
    assert body["sung"] == [
        {
            "song_key": "invented band|first song",
            "artist": "Invented Band",
            "title": "First Song",
            "times_sung": 1,
            "last_sung_at": "2026-10-21T21:00:00+00:00",
            "source": "karaoke_log",
        }
    ]
    assert body["unresolved"] == [{"artist": "Other Act", "title": "Second Song", "times_sung": 1}]

    empty = client.get("/api/test/karaoke-log", params={"email": "nobody@example.com"}).json()
    assert empty == {"user_id": None, "entries": [], "sung": [], "unresolved": []}

    assert client.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 204
    assert list(fs.cols["karaoke_log"]) == ["someone_else:kj_1"]
    assert not fs.cols["user_karaoke_unresolved"]
