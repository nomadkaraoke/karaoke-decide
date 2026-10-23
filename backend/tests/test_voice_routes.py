"""Voice profile routes: /api/my/voice and the local-stack voice seeding."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api.deps import get_auth_service_dep, get_current_user, get_firestore, get_settings
from backend.config import BackendSettings
from backend.main import app
from backend.services.auth_service import AuthService
from backend.services.gen_session import GenIdentity
from backend.services.voice_profile_service import VoiceProfileService
from backend.tests.test_karaoke_log_routes import RouteFirestore
from karaoke_decide.core.models import User

EMAIL = "singer@example.com"
EMAIL_HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
USER_ID = "user_voice_a"
GEN_TOKEN = "g" * 43

PROFILE: dict[str, Any] = {
    "low_midi": 40,
    "high_midi": 69,
    "comfort_low": 45,
    "comfort_high": 64,
    "voice_type": "baritone",
    "voice_type_between": "tenor",
    "register": None,
    "confidence": "medium",
    "method": "mic",
    "measured_at": "2026-10-22T10:00:00Z",
}


@pytest.fixture(autouse=True)
def legacy_jwt_token_path() -> None:
    """Use the real gen-token detection here."""


@pytest.fixture
def fs() -> RouteFirestore:
    fs = RouteFirestore()
    fs.cols["decide_users"] = {EMAIL_HASH: {"user_id": USER_ID, "email": EMAIL, "is_guest": False}}
    return fs


def _settings() -> BackendSettings:
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
    )


def _client(fs: RouteFirestore, user: User | None) -> TestClient:
    settings = _settings()
    auth = AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_auth_service_dep] = lambda: auth
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def client(fs: RouteFirestore) -> Iterator[TestClient]:
    yield _client(fs, User(id=USER_ID, email=EMAIL))
    app.dependency_overrides.clear()


@pytest.fixture
def anon(fs: RouteFirestore) -> Iterator[TestClient]:
    validator = MagicMock()
    validator.validate = AsyncMock(return_value=GenIdentity(email=EMAIL))
    with patch("backend.api.deps.get_gen_session_validator", return_value=validator):
        yield _client(fs, None)
    app.dependency_overrides.clear()


def test_get_none_when_unset(client: TestClient) -> None:
    r = client.get("/api/my/voice")
    assert r.status_code == 200 and r.json() == {"profile": None}


def test_put_then_get(client: TestClient, fs: RouteFirestore) -> None:
    r = client.put("/api/my/voice", json=PROFILE)
    assert r.status_code == 200, r.text
    put = r.json()
    assert put["voice_type"] == "baritone" and put["comfort_high"] == 64 and put["updated_at"]
    assert client.get("/api/my/voice").json() == {"profile": put}
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["low_midi"] == 40


def test_put_overwrites_even_with_older_measured_at(client: TestClient) -> None:
    client.put("/api/my/voice", json=PROFILE)
    older = {**PROFILE, "measured_at": "2026-01-01T00:00:00Z", "low_midi": 41}
    assert client.put("/api/my/voice", json=older).status_code == 200
    assert client.get("/api/my/voice").json()["profile"]["low_midi"] == 41


def test_delete(client: TestClient) -> None:
    client.put("/api/my/voice", json=PROFILE)
    assert client.delete("/api/my/voice").status_code == 204
    assert client.get("/api/my/voice").json() == {"profile": None}
    assert client.delete("/api/my/voice").status_code == 204  # idempotent


def test_rejects_extra_fields(client: TestClient, fs: RouteFirestore) -> None:
    """Privacy pin: mic audio never reaches the server."""
    assert client.put("/api/my/voice", json={**PROFILE, "samples": [0.1]}).status_code == 422
    assert "voice_profile" not in fs.cols["decide_users"][EMAIL_HASH]


def test_rejects_comfort_outside_range(client: TestClient) -> None:
    for bad in (
        {"comfort_low": 39},  # below low_midi
        {"comfort_high": 70},  # above high_midi
        {"comfort_low": 60, "comfort_high": 50},
        {"low_midi": 10},  # outside 24-96
        {"high_midi": 97},
    ):
        assert client.put("/api/my/voice", json={**PROFILE, **bad}).status_code == 422, bad


def test_self_requires_low_confidence(client: TestClient) -> None:
    self_ = {**PROFILE, "method": "self"}
    assert client.put("/api/my/voice", json=self_).status_code == 422
    assert client.put("/api/my/voice", json={**self_, "confidence": "low"}).status_code == 200


def test_requires_type_or_register(client: TestClient) -> None:
    neither = {**PROFILE, "voice_type": None, "voice_type_between": None}
    both = {**PROFILE, "register": "middle"}
    assert client.put("/api/my/voice", json=neither).status_code == 422
    assert client.put("/api/my/voice", json=both).status_code == 422
    only_register = {**neither, "register": "higher"}
    assert client.put("/api/my/voice", json=only_register).status_code == 200


def test_requires_auth(anon: TestClient) -> None:
    for method in ("GET", "PUT", "DELETE"):
        r = anon.request(method, "/api/my/voice", json=PROFILE if method == "PUT" else None)
        assert r.status_code == 401, method


def test_gen_session_user_profile_doc(anon: TestClient, fs: RouteFirestore) -> None:
    """A gen-token user lands on decide_users/{sha256(email)} and the service reads it by user_id."""
    fs.cols["decide_users"] = {}
    bearer = {"Authorization": f"Bearer {GEN_TOKEN}"}
    assert anon.get("/api/my/voice", headers=bearer).json() == {"profile": None}
    assert anon.put("/api/my/voice", json=PROFILE, headers=bearer).status_code == 200
    doc = fs.cols["decide_users"][EMAIL_HASH]
    assert doc["voice_profile"]["voice_type"] == "baritone"
    assert anon.get("/api/my/voice", headers=bearer).json()["profile"]["low_midi"] == 40


@pytest.mark.asyncio
async def test_service_get_for_recommendations(fs: RouteFirestore) -> None:
    svc = VoiceProfileService(fs)  # type: ignore[arg-type]
    assert await svc.get(USER_ID) is None
    assert await svc.get("user_missing") is None


def _test_app(monkeypatch: pytest.MonkeyPatch, flag: bool) -> TestClient:
    s = BackendSettings(environment="development", decide_test_api=flag, google_cloud_project="test-project")
    monkeypatch.setattr(main, "get_backend_settings", lambda: s)
    return TestClient(main.create_app())


def test_test_api_seed_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests.test_test_support import FakeFirestore

    assert (
        _test_app(monkeypatch, False)
        .post("/api/test/voice/seed", json={"email": EMAIL, "profile": PROFILE})
        .status_code
        == 404
    )
    c = _test_app(monkeypatch, True)
    fake = FakeFirestore()
    c.app.dependency_overrides[get_firestore] = lambda: fake  # type: ignore[attr-defined]
    r = c.post("/api/test/voice/seed", json={"email": EMAIL, "profile": PROFILE})
    assert r.status_code == 204
    assert c.get("/api/test/profile", params={"email": EMAIL}).json()["voice_profile"]["voice_type"] == "baritone"
    assert (
        c.post("/api/test/voice/seed", json={"email": EMAIL, "profile": {**PROFILE, "samples": []}}).status_code == 422
    )
    assert c.get("/api/test/profile", params={"email": "none@example.com"}).json()["voice_profile"] is None


def test_register_key_is_register_in_json_and_firestore(client: TestClient, fs: RouteFirestore) -> None:
    body = {**PROFILE, "voice_type": None, "voice_type_between": None, "register": "middle"}
    put = client.put("/api/my/voice", json=body).json()
    assert put["register"] == "middle" and "register_" not in put
    stored = fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]
    assert stored["register"] == "middle" and "register_" not in stored
    got = client.get("/api/my/voice").json()["profile"]
    assert got["register"] == "middle" and "register_" not in got


@pytest.mark.asyncio
async def test_invalid_stored_profile_is_unset(fs: RouteFirestore, caplog: pytest.LogCaptureFixture) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = {"low_midi": 1, "junk": True}
    svc = VoiceProfileService(fs)  # type: ignore[arg-type]
    with caplog.at_level("WARNING"):
        assert await svc.get(USER_ID) is None
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_get_route_survives_invalid_stored_profile(client: TestClient, fs: RouteFirestore) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = {"low_midi": 1}
    assert client.get("/api/my/voice").json() == {"profile": None}


def test_put_404_when_user_has_no_doc(client: TestClient, fs: RouteFirestore) -> None:
    fs.cols["decide_users"].clear()
    r = client.put("/api/my/voice", json=PROFILE)
    assert r.status_code == 404 and r.json() == {"detail": "not_found"}
