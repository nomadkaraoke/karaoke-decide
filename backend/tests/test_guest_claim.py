"""Tests for POST /api/auth/claim-guest and GuestClaimService."""

import hashlib
from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings
from backend.services.auth_service import AuthService
from backend.services.gen_session import GenIdentity

GEN_TOKEN = "g" * 43
EMAIL = "ann@x.com"
EMAIL_HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
TARGET = "user_target"
GUEST = "guest_abc"
TS = "2024-01-01T12:00:00+00:00"


class FakeFirestore:
    """Minimal in-memory Firestore with the methods the claim service uses."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    def put(self, col: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols.setdefault(col, {})[doc_id] = dict(data)

    async def get_document(self, col: str, doc_id: str) -> dict[str, Any] | None:
        d = self.cols.get(col, {}).get(doc_id)
        return None if d is None else {"id": doc_id, **d}

    async def set_document(self, col: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        if merge:
            self.cols.setdefault(col, {}).setdefault(doc_id, {}).update(data)
        else:
            self.put(col, doc_id, data)

    async def create_document(self, col: str, doc_id: str, data: dict[str, Any]) -> bool:
        if doc_id in self.cols.get(col, {}):
            return False
        self.put(col, doc_id, data)
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
        self, col: str, filters: list[tuple[str, str, Any]] | None = None, limit: int | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        out = []
        for doc_id, d in self.cols.get(col, {}).items():
            if all(d.get(f) == v for f, _op, v in filters or []):
                out.append({"id": doc_id, **d})
        return out[:limit] if limit else out


@pytest.fixture(autouse=True)
def legacy_jwt_token_path() -> None:
    """Override the conftest autouse fixture: use the real gen-token detection here."""


@pytest.fixture
def fs() -> FakeFirestore:
    f = FakeFirestore()
    f.put(
        "decide_users",
        EMAIL_HASH,
        {"user_id": TARGET, "email": EMAIL, "is_guest": False, "created_at": TS, "updated_at": TS},
    )
    f.put("decide_users", GUEST, {"user_id": GUEST, "is_guest": True, "created_at": TS, "updated_at": TS})
    return f


@pytest.fixture
def auth(fs: FakeFirestore) -> AuthService:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
    )
    return AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]


@pytest.fixture
def api(auth: AuthService, fs: FakeFirestore) -> Generator[TestClient, None, None]:
    validator = MagicMock()
    validator.validate = AsyncMock(return_value=GenIdentity(email=EMAIL))
    with (
        patch("backend.api.deps.get_auth_service", return_value=auth),
        patch("backend.api.deps.get_gen_session_validator", return_value=validator),
        patch("backend.api.deps.FirestoreService", return_value=fs),
    ):
        from backend.main import app

        yield TestClient(app)


def guest_jwt(auth: AuthService, user_id: str = GUEST, guest: bool = True) -> str:
    from karaoke_decide.core.models import User

    user = User(id=user_id, is_guest=guest, email=None if guest else EMAIL)
    return auth.generate_guest_jwt(user)[0] if guest else auth.generate_jwt(user)[0]


def claim(api: TestClient, token: str, bearer: str = GEN_TOKEN) -> Any:
    return api.post(
        "/api/auth/claim-guest",
        json={"guest_token": token},
        headers={"Authorization": f"Bearer {bearer}"},
    )


def test_claim_moves_all_collections(api: TestClient, fs: FakeFirestore, auth: AuthService) -> None:
    for i in range(1200):
        fs.put("user_songs", f"{GUEST}:s{i}", {"user_id": GUEST, "song_id": f"s{i}", "play_count": 1})
    for n in ("a", "b", "c"):
        fs.put("user_artists", f"{GUEST}:manual:{n}", {"user_id": GUEST, "artist_name": n})
    fs.put("music_services", f"{GUEST}_spotify", {"user_id": GUEST, "service_type": "spotify"})
    fs.put("playlists", "pl1", {"user_id": GUEST, "name": "mine"})
    fs.put("quiz_progress", GUEST, {"user_id": GUEST, "step": 2})
    fs.cols["decide_users"][GUEST].update(
        quiz_completed_at=TS, quiz_genres=["rock"], quiz_decades=["1990s"], quiz_energy_pref="high"
    )

    resp = claim(api, guest_jwt(auth))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "songs_moved": 1200,
        "artists_moved": 3,
        "services_moved": 1,
        "playlists_moved": 1,
        "quiz_copied": True,
    }
    songs = fs.cols["user_songs"]
    assert len(songs) == 1200
    assert all(k.startswith(f"{TARGET}:") and v["user_id"] == TARGET for k, v in songs.items())
    assert set(fs.cols["user_artists"]) == {f"{TARGET}:manual:{n}" for n in "abc"}
    assert set(fs.cols["music_services"]) == {f"{TARGET}_spotify"}
    assert fs.cols["music_services"][f"{TARGET}_spotify"]["user_id"] == TARGET
    assert fs.cols["playlists"]["pl1"]["user_id"] == TARGET
    assert set(fs.cols["quiz_progress"]) == {TARGET}
    target = fs.cols["decide_users"][EMAIL_HASH]
    assert target["quiz_genres"] == ["rock"] and target["quiz_energy_pref"] == "high"
    assert GUEST not in fs.cols["decide_users"]


def test_claim_merges_song_collision_play_counts(api: TestClient, fs: FakeFirestore, auth: AuthService) -> None:
    fs.put("user_songs", f"{GUEST}:x", {"user_id": GUEST, "song_id": "x", "play_count": 2, "created_at": "2024-03-01"})
    fs.put(
        "user_songs", f"{TARGET}:x", {"user_id": TARGET, "song_id": "x", "play_count": 3, "created_at": "2024-06-01"}
    )
    resp = claim(api, guest_jwt(auth))
    assert resp.status_code == 200
    merged = fs.cols["user_songs"][f"{TARGET}:x"]
    assert merged["play_count"] == 5
    assert merged["created_at"] == "2024-03-01"
    assert f"{GUEST}:x" not in fs.cols["user_songs"]


def test_claim_keeps_target_quiz_when_present_and_unions_genres(
    api: TestClient, fs: FakeFirestore, auth: AuthService
) -> None:
    fs.cols["decide_users"][EMAIL_HASH].update(
        quiz_completed_at=TS,
        quiz_genres=["rock"],
        quiz_decades=["1980s"],
        quiz_energy_pref="chill",
        quiz_artist_affinities=[{"artist_name": "A", "affinity": "love"}],
    )
    fs.cols["decide_users"][GUEST].update(
        quiz_completed_at=TS,
        quiz_genres=["pop", "rock"],
        quiz_decades=["1990s"],
        quiz_energy_pref="high",
        quiz_artist_affinities=[
            {"artist_name": "A", "affinity": "like"},
            {"artist_name": "B", "affinity": "like"},
        ],
    )
    resp = claim(api, guest_jwt(auth))
    assert resp.status_code == 200
    assert resp.json()["quiz_copied"] is False
    target = fs.cols["decide_users"][EMAIL_HASH]
    assert target["quiz_energy_pref"] == "chill"
    assert target["quiz_genres"] == ["rock", "pop"]
    assert target["quiz_decades"] == ["1980s", "1990s"]
    assert target["quiz_artist_affinities"] == [
        {"artist_name": "A", "affinity": "love"},
        {"artist_name": "B", "affinity": "like"},
    ]


def test_claim_keeps_targets_existing_service_and_quiz_progress(
    api: TestClient, fs: FakeFirestore, auth: AuthService
) -> None:
    fs.put("music_services", f"{GUEST}_spotify", {"user_id": GUEST, "token": "guest"})
    fs.put("music_services", f"{TARGET}_spotify", {"user_id": TARGET, "token": "target"})
    fs.put("quiz_progress", GUEST, {"user_id": GUEST, "step": 1})
    fs.put("quiz_progress", TARGET, {"user_id": TARGET, "step": 3})
    resp = claim(api, guest_jwt(auth))
    assert resp.json()["services_moved"] == 0
    assert fs.cols["music_services"] == {f"{TARGET}_spotify": {"user_id": TARGET, "token": "target"}}
    assert fs.cols["quiz_progress"] == {TARGET: {"user_id": TARGET, "step": 3}}


def test_claim_rejects_verified_jwt(api: TestClient, fs: FakeFirestore, auth: AuthService) -> None:
    resp = claim(api, guest_jwt(auth, user_id="user_other", guest=False))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_a_guest_token"
    resp = claim(api, "garbage.token.value")
    assert resp.status_code == 400
    assert GUEST in fs.cols["decide_users"]


def test_claim_twice_404(api: TestClient, auth: AuthService) -> None:
    token = guest_jwt(auth)
    assert claim(api, token).status_code == 200
    again = claim(api, token)
    assert again.status_code == 404
    assert again.json()["detail"] == "guest_not_found"


def test_claim_requires_gen_bearer(api: TestClient, fs: FakeFirestore, auth: AuthService) -> None:
    jwt_bearer = guest_jwt(auth, user_id=TARGET, guest=False)
    # Pin the date: after the legacy JWT sunset this would be 401, not 403.
    with patch("backend.api.deps._today", return_value=date(2026, 11, 30)):
        resp = claim(api, guest_jwt(auth), bearer=jwt_bearer)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "requires_gen_session"
    assert GUEST in fs.cols["decide_users"]
