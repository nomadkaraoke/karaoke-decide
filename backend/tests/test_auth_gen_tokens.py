"""Tests for gen session tokens in get_current_user and gen profile resolution."""

import hashlib
from collections.abc import Generator
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings
from backend.services.auth_service import AuthService
from backend.services.gen_session import GenIdentity, GenSessionError

GEN_TOKEN = "g" * 43
EMAIL = "ann@x.com"
EMAIL_HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
DOC_TS = "2024-01-01T12:00:00+00:00"


@pytest.fixture(autouse=True)
def legacy_jwt_token_path() -> None:
    """Override the conftest autouse fixture: use the real gen-token detection here."""


@pytest.fixture
def firestore() -> MagicMock:
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value=None)
    fs.set_document = AsyncMock(return_value=None)
    fs.create_document = AsyncMock(return_value=True)
    fs.update_document = AsyncMock(return_value=None)
    fs.query_documents = AsyncMock(return_value=[])
    return fs


@pytest.fixture
def validator() -> MagicMock:
    v = MagicMock()
    v.validate = AsyncMock(return_value=GenIdentity(email=EMAIL))
    return v


@pytest.fixture
def real_auth_client(firestore: MagicMock, validator: MagicMock) -> Generator[TestClient, None, None]:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
    )
    auth = AuthService(settings, firestore, email_service=MagicMock())
    with (
        patch("backend.api.deps.get_auth_service", return_value=auth),
        patch("backend.api.deps.get_gen_session_validator", return_value=validator),
    ):
        from backend.main import app

        yield TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _decide_doc(**extra: object) -> dict[str, object]:
    return {
        "user_id": "user_abc",
        "email": EMAIL,
        "display_name": "Ann",
        "created_at": DOC_TS,
        "updated_at": DOC_TS,
        **extra,
    }


def test_gen_token_new_user_creates_profile(real_auth_client: TestClient, firestore: MagicMock) -> None:
    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_guest"] is False
    assert body["email"] == EMAIL
    assert body["id"].startswith("user_")
    firestore.create_document.assert_awaited_once()
    coll, doc_id, data = firestore.create_document.await_args.args
    assert (coll, doc_id) == ("decide_users", EMAIL_HASH)
    assert data["gen_linked_at"] and data["is_guest"] is False and data["user_id"] == body["id"]


def test_gen_token_existing_decide_user_keeps_id(real_auth_client: TestClient, firestore: MagicMock) -> None:
    doc = _decide_doc()

    async def get_document(collection: str, doc_id: str) -> dict[str, object] | None:
        return doc if (collection, doc_id) == ("decide_users", EMAIL_HASH) else None

    async def update_document(collection: str, doc_id: str, data: dict[str, object]) -> None:
        doc.update(data)

    firestore.get_document.side_effect = get_document
    firestore.update_document.side_effect = update_document

    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.status_code == 200
    assert resp.json()["id"] == "user_abc"
    firestore.create_document.assert_not_called()
    firestore.update_document.assert_awaited_once()
    assert firestore.update_document.await_args.args[:2] == ("decide_users", EMAIL_HASH)
    assert "gen_linked_at" in firestore.update_document.await_args.args[2]

    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.json()["id"] == "user_abc"
    firestore.update_document.assert_awaited_once()  # not written again


def test_gen_token_does_not_import_gen_admin(real_auth_client: TestClient, firestore: MagicMock) -> None:
    firestore.get_document.return_value = _decide_doc(gen_linked_at=DOC_TS, is_admin=False)
    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.json()["is_admin"] is False


def test_gen_token_invalid_401(real_auth_client: TestClient, validator: MagicMock) -> None:
    validator.validate.side_effect = GenSessionError("expired")
    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "gen_session_expired"


def _legacy_token() -> str:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
    )
    token, _ = AuthService(settings, MagicMock(), email_service=MagicMock()).generate_jwt(
        MagicMock(id="user_abc", email=EMAIL)
    )
    return str(token)


def test_legacy_jwt_still_valid_before_sunset(real_auth_client: TestClient, firestore: MagicMock) -> None:
    token = _legacy_token()
    assert "." in token
    firestore.query_documents.return_value = [_decide_doc()]
    with patch("backend.api.deps._today", return_value=date(2026, 11, 30)):
        resp = real_auth_client.get("/api/auth/me", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == "user_abc"


def test_legacy_jwt_rejected_after_sunset(real_auth_client: TestClient, firestore: MagicMock) -> None:
    token = _legacy_token()
    firestore.query_documents.return_value = [_decide_doc()]
    with patch("backend.api.deps._today", return_value=date(2026, 12, 1)):
        resp = real_auth_client.get("/api/auth/me", headers=_bearer(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "legacy_token_expired"


def test_gen_token_still_valid_after_sunset(real_auth_client: TestClient) -> None:
    with patch("backend.api.deps._today", return_value=date(2027, 1, 1)):
        resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.status_code == 200


def test_gen_user_passes_verified_user_dep(real_auth_client: TestClient, firestore: MagicMock) -> None:
    from backend.api.deps import get_music_service_service_dep
    from backend.main import app

    music = MagicMock()
    music.get_user_services = AsyncMock(return_value=[])
    music.get_source_summaries = AsyncMock(return_value=[])
    app.dependency_overrides[get_music_service_service_dep] = lambda: music
    try:
        resp = real_auth_client.get("/api/services", headers=_bearer(GEN_TOKEN))
    finally:
        app.dependency_overrides.pop(get_music_service_service_dep, None)
    assert resp.status_code == 200


def test_gen_first_sign_in_race_returns_same_user_id(real_auth_client: TestClient, firestore: MagicMock) -> None:
    stored: dict[str, dict[str, object]] = {}

    async def create_document(collection: str, doc_id: str, data: dict[str, object]) -> bool:
        if stored:
            return False
        stored["doc"] = data
        return True

    # Both requests read "absent" first (stale); only the first create wins.
    stale_reads = 2

    async def racing_get(collection: str, doc_id: str) -> dict[str, object] | None:
        nonlocal stale_reads
        if stale_reads > 0:
            stale_reads -= 1
            return None
        return stored.get("doc")

    firestore.get_document.side_effect = racing_get
    firestore.create_document.side_effect = create_document

    first = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN)).json()["id"]
    second = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN)).json()["id"]
    assert first == second
    assert firestore.create_document.await_count == 2


def test_legacy_doc_without_timestamps_does_not_raise(real_auth_client: TestClient, firestore: MagicMock) -> None:
    firestore.get_document.return_value = {"user_id": "user_old", "email": EMAIL, "gen_linked_at": DOC_TS}
    resp = real_auth_client.get("/api/auth/me", headers=_bearer(GEN_TOKEN))
    assert resp.status_code == 200
    assert resp.json()["id"] == "user_old"
