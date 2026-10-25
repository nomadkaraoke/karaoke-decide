"""GET /api/my/library: payload, privacy and ETag / 304 (in-memory Firestore)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalog_store_dep, get_current_user, get_firestore
from backend.main import app
from backend.services.song_feedback_service import SongFeedbackService
from backend.tests.test_karaoke_log_service import FakeStore, MemoryFirestore
from backend.tests.test_library_service import LOUD, USER, _put
from karaoke_decide.core.models import User


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.fixture
def client(fs: MemoryFirestore) -> Iterator[TestClient]:
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_catalog_store_dep] = lambda: FakeStore(None)
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="singer@example.com")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_library_etag_304(client: TestClient, fs: MemoryFirestore) -> None:
    _put(fs, "app_1")
    r = client.get("/api/my/library")
    assert r.status_code == 200
    etag = r.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert r.json()["etag"] == etag.strip('"')
    assert [i["key"] for i in r.json()["items"]] == [LOUD]

    for sent in (etag, etag.strip('"')):
        r2 = client.get("/api/my/library", headers={"If-None-Match": sent})
        assert r2.status_code == 304 and r2.content == b""

    import asyncio

    asyncio.run(SongFeedbackService(fs).love(USER, LOUD, "The Lanterns", "Paper Harbour"))  # type: ignore[arg-type]
    r3 = client.get("/api/my/library", headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["ETag"] != etag
    assert r3.json()["items"][0]["loved"] is True


def test_library_hides_private_fields(client: TestClient, fs: MemoryFirestore) -> None:
    _put(fs, "app_1")
    fs.cols["user_songs"] = {}
    text = client.get("/api/my/library").text
    assert "user_id" not in text and "email_hash" not in text


def test_library_requires_auth() -> None:
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/my/library").status_code in (401, 403)
