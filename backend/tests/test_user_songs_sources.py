"""UserSong.source must accept every source value the backend writes (GET /api/my/songs 500 regression)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_recommendation_service_dep
from backend.config import BackendSettings
from backend.main import app
from backend.services.recommendation_service import RecommendationService
from backend.tests.test_karaoke_log_service import MemoryFirestore
from karaoke_decide.core.models import User, UserSong

USER_ID = "user_songs_a"


class SongsFirestore(MemoryFirestore):
    async def count_documents(self, collection: str, filters: Any = None, **_: Any) -> int:
        return len(await self.query_documents(collection, filters=filters))


@pytest.fixture
def client() -> Iterator[tuple[TestClient, SongsFirestore]]:
    fs = SongsFirestore()
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
    )
    service = RecommendationService(settings, fs)  # type: ignore[arg-type]
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, email="s@example.com")
    app.dependency_overrides[get_recommendation_service_dep] = lambda: service
    yield TestClient(app), fs
    app.dependency_overrides.clear()


def test_get_user_songs_with_karaoke_log_row(client: tuple[TestClient, SongsFirestore]) -> None:
    c, fs = client
    fs.cols["user_songs"] = {
        f"{USER_ID}:adele|hello": {
            "user_id": USER_ID,
            "song_id": "adele|hello",
            "song_key": "adele|hello",
            "source": "karaoke_log",
            "artist": "Adele",
            "title": "Hello",
            "times_sung": 2,
        }
    }
    r = c.get("/api/my/songs")
    assert r.status_code == 200, r.text
    songs = r.json()["songs"]
    assert len(songs) == 1
    assert songs[0]["source"] == "karaoke_log"
    assert songs[0]["times_sung"] == 2


@pytest.mark.parametrize("source", ["karaoke_log", "manual", "voice_session"])
def test_user_song_accepts_every_written_source(source: str) -> None:
    song = UserSong(id="u:s", user_id="u", song_id="s", source=source, artist="A", title="T")  # type: ignore[arg-type]
    assert song.source == source
