"""ListenBrainz connect via MetaBrainz OAuth, and service tokens encrypted at rest."""

from __future__ import annotations

import copy
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_music_service_service_dep, get_settings
from backend.config import BackendSettings
from backend.services.music_service_service import MusicServiceError, MusicServiceService
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import User
from karaoke_decide.services.listenbrainz import ListenBrainzUserInfo
from karaoke_decide.services.metabrainz_oauth import MetaBrainzInvalidGrantError, MetaBrainzOAuth

USER = "fixture-user"
KEY = "1GoNVOX3pVL8JknEuhjFmFMF9Q-9_KKmTB3oUi_3vAo="  # test-only Fernet key
LB_DOC = f"{USER}_listenbrainz"


class FakeDb:
    """In-memory Firestore for the calls MusicServiceService makes."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    def doc(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.cols.get(collection, {}).get(doc_id)

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        d = self.doc(collection, doc_id)
        return copy.deepcopy(d) if d is not None else None

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols.setdefault(collection, {})[doc_id] = copy.deepcopy(data)

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols.setdefault(collection, {}).setdefault(doc_id, {}).update(copy.deepcopy(data))

    async def delete_document_atomically(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.cols.get(collection, {}).pop(doc_id, None)


def _settings(**over: Any) -> BackendSettings:
    base: dict[str, Any] = {
        "listenbrainz_oauth_enabled": True,
        "metabrainz_client_id": "mb-client",
        "metabrainz_client_secret": "mb-secret",
        "decide_token_key": KEY,
        "public_api_base_url": "https://decide.example",
        "frontend_url": "https://web.example",
    }
    return BackendSettings(**{**base, **over})


def _metabrainz(settings: BackendSettings) -> MagicMock:
    real = MetaBrainzOAuth(settings)
    mb = MagicMock()
    mb.authorize_url = real.authorize_url
    mb.exchange_code = AsyncMock(
        return_value={"access_token": "meba_access", "refresh_token": "meba_refresh", "expires_in": 3600}
    )
    mb.refresh = AsyncMock(
        return_value={"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}
    )
    mb.userinfo = AsyncMock(return_value="e2e-listener")
    return mb


def _listenbrainz() -> MagicMock:
    lb = MagicMock()
    lb.get_user_listen_count = AsyncMock(return_value=42)
    lb.get_user_info = AsyncMock(return_value=ListenBrainzUserInfo(username="e2e-listener", listen_count=42))
    return lb


def _service(db: FakeDb, settings: BackendSettings | None = None) -> MusicServiceService:
    settings = settings or _settings()
    return MusicServiceService(
        settings,
        db,  # type: ignore[arg-type]
        listenbrainz_client=_listenbrainz(),
        metabrainz_oauth=_metabrainz(settings),
    )


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def make_client(db: FakeDb) -> Generator[Any, None, None]:
    from backend.main import app

    def make(settings: BackendSettings | None = None) -> tuple[TestClient, MusicServiceService]:
        settings = settings or _settings()
        svc = _service(db, settings)
        app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="fixture@example.com")
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_music_service_service_dep] = lambda: svc
        return TestClient(app), svc

    try:
        yield make
    finally:
        app.dependency_overrides.clear()


def _start(client: TestClient, return_to: str = "app") -> str:
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": return_to})
    assert resp.status_code == 200, resp.text
    return str(parse_qs(urlparse(resp.json()["auth_url"]).query)["state"][0])


def _callback(client: TestClient, **params: str) -> Any:
    return client.get("/api/services/listenbrainz/oauth/callback", params=params, follow_redirects=False)


# --- start ------------------------------------------------------------------------------------


def test_start_disabled_404(make_client: Any) -> None:
    client, _ = make_client(_settings(decide_token_key=""))
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": "app"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "oauth_disabled"


def test_start_builds_pkce_url(make_client: Any, db: FakeDb) -> None:
    client, _ = make_client()
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": "web"})
    url = resp.json()["auth_url"]
    assert url.startswith("https://metabrainz.org/oauth2/authorize?")
    assert "scope=profile%20listenbrainz%3Asubmit-listens" in url
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["client_id"] == ["mb-client"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://decide.example/api/services/listenbrainz/oauth/callback"]
    state_doc = db.doc("oauth_states", q["state"][0])
    assert state_doc is not None
    assert state_doc["user_id"] == USER
    assert state_doc["service_type"] == "listenbrainz"
    assert state_doc["extra"]["return_to"] == "web"
    assert len(state_doc["extra"]["code_verifier"]) >= 43
    assert q["code_challenge"][0] != state_doc["extra"]["code_verifier"]


def test_start_rejects_unknown_return_to(make_client: Any) -> None:
    client, _ = make_client()
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": "evil"})
    assert resp.status_code == 422


# --- callback ---------------------------------------------------------------------------------


def test_callback_app_redirects_to_scheme(make_client: Any, db: FakeDb) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    code_verifier = db.doc("oauth_states", state)["extra"]["code_verifier"]  # type: ignore[index]

    resp = _callback(client, code="the-code", state=state)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("nomadkaraoke://services/listenbrainz?status=connected")
    assert resp.headers["cache-control"] == "no-store"
    assert "Return to the app" in resp.text
    svc.metabrainz.exchange_code.assert_awaited_once_with("the-code", code_verifier)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None
    assert stored["service_username"] == "e2e-listener"
    assert stored["auth"] == "oauth"
    assert stored["error_code"] is None
    assert db.doc("oauth_states", state) is None  # consumed


def test_callback_web_redirects_to_frontend(make_client: Any) -> None:
    client, _ = make_client()
    state = _start(client, "web")
    resp = _callback(client, code="c", state=state)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://web.example/en/music-i-know?listenbrainz=connected"


def test_callback_error_uses_reason_code(make_client: Any) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    svc.metabrainz.exchange_code.side_effect = ExternalServiceError("MetaBrainz", "secret internals http://x")
    resp = _callback(client, code="c", state=state)
    loc = resp.headers["location"]
    assert loc == "nomadkaraoke://services/listenbrainz?status=error&reason=exchange_failed"
    assert "internals" not in resp.text


def test_callback_denied(make_client: Any) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    resp = _callback(client, error="access_denied", state=state)
    assert resp.headers["location"] == "nomadkaraoke://services/listenbrainz?status=error&reason=denied"
    svc.metabrainz.exchange_code.assert_not_awaited()


def test_callback_missing_params(make_client: Any) -> None:
    client, _ = make_client()
    state = _start(client, "web")
    resp = _callback(client, state=state)
    assert resp.headers["location"] == "https://web.example/en/music-i-know?listenbrainz=error&reason=missing_params"


def test_callback_invalid_state(make_client: Any) -> None:
    client, svc = make_client()
    resp = _callback(client, code="c", state="forged")
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("listenbrainz=error&reason=invalid_state")
    svc.metabrainz.exchange_code.assert_not_awaited()


def test_callback_state_of_another_service_is_invalid(make_client: Any) -> None:
    client, svc = make_client()
    import asyncio

    state = asyncio.run(svc.create_oauth_state(USER, "spotify"))
    resp = _callback(client, code="c", state=state)
    assert resp.headers["location"].endswith("reason=invalid_state")


def test_callback_validate_failed(make_client: Any) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    svc.listenbrainz.get_user_listen_count.side_effect = ExternalServiceError("ListenBrainz", "User not found")
    resp = _callback(client, code="c", state=state)
    assert resp.headers["location"] == "nomadkaraoke://services/listenbrainz?status=error&reason=validate_failed"


# --- tokens at rest -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokens_encrypted_at_rest(db: FakeDb) -> None:
    svc = _service(db)
    service = await svc.create_listenbrainz_oauth_service(
        USER, {"access_token": "meba_a", "refresh_token": "meba_r", "expires_in": 3600}, "e2e-listener"
    )
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None
    assert stored["access_token"].startswith("enc:v1:")
    assert stored["refresh_token"].startswith("enc:v1:")
    assert "meba_a" not in str(stored)
    assert service.access_token == "meba_a"  # callers get plaintext
    assert (await svc.get_service(USER, "listenbrainz")).access_token == "meba_a"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_spotify_tokens_encrypted_on_write(db: FakeDb) -> None:
    svc = _service(db)
    await svc.create_or_update_spotify_service(
        USER, {"access_token": "sp_a", "refresh_token": "sp_r", "expires_in": 3600}, {"id": "sp-user"}
    )
    stored = db.doc("music_services", f"{USER}_spotify")
    assert stored is not None
    assert stored["access_token"].startswith("enc:v1:")
    assert stored["refresh_token"].startswith("enc:v1:")
    assert (await svc.get_service(USER, "spotify")).refresh_token == "sp_r"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_spotify_tokens_read_legacy_plaintext(db: FakeDb) -> None:
    svc = _service(db)
    db.cols["music_services"] = {
        f"{USER}_spotify": {
            "id": f"{USER}_spotify",
            "user_id": USER,
            "service_type": "spotify",
            "service_user_id": "sp",
            "service_username": "sp",
            "access_token": "legacy-plain",
            "refresh_token": "legacy-refresh",
            "token_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    }
    service = await svc.get_service(USER, "spotify")
    assert service is not None
    assert await svc.get_valid_spotify_token(service) == "legacy-plain"


@pytest.mark.asyncio
async def test_no_key_keeps_tokens_plaintext(db: FakeDb) -> None:
    svc = _service(db, _settings(decide_token_key=""))
    await svc.create_or_update_spotify_service(
        USER, {"access_token": "sp_a", "refresh_token": "sp_r", "expires_in": 3600}, {"id": "sp-user"}
    )
    assert db.doc("music_services", f"{USER}_spotify")["access_token"] == "sp_a"  # type: ignore[index]


# --- refresh ------------------------------------------------------------------------------------


async def _lb_service(db: FakeDb, svc: MusicServiceService, expires_in: int) -> Any:
    await svc.create_listenbrainz_oauth_service(
        USER, {"access_token": "meba_old", "refresh_token": "meba_r", "expires_in": expires_in}, "e2e-listener"
    )
    return await svc.get_service(USER, "listenbrainz")


@pytest.mark.asyncio
async def test_lb_token_fresh_is_returned_without_refresh(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 3600)
    assert await svc.get_listenbrainz_token(service) == "meba_old"
    svc.metabrainz.refresh.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lb_token_refreshes_near_expiry_and_stores_rotated_tokens(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    assert await svc.get_listenbrainz_token(service) == "meba_access2"
    svc.metabrainz.refresh.assert_awaited_once_with("meba_r")  # type: ignore[attr-defined]
    reread = await svc.get_service(USER, "listenbrainz")
    assert reread.access_token == "meba_access2"  # type: ignore[union-attr]
    assert reread.refresh_token == "meba_refresh2"  # type: ignore[union-attr]
    assert db.doc("music_services", LB_DOC)["refresh_token"].startswith("enc:v1:")  # type: ignore[index]


@pytest.mark.asyncio
async def test_refresh_invalid_grant_marks_needs_reconnect(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    svc.metabrainz.refresh.side_effect = MetaBrainzInvalidGrantError()  # type: ignore[attr-defined]
    with pytest.raises(MusicServiceError):
        await svc.get_listenbrainz_token(service)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None
    assert stored["sync_status"] == "error"
    assert stored["error_code"] == "needs_reconnect"


@pytest.mark.asyncio
async def test_username_connection_has_no_token(db: FakeDb) -> None:
    svc = _service(db)
    await svc.create_listenbrainz_service(USER, "e2e-listener")
    service = await svc.get_service(USER, "listenbrainz")
    with pytest.raises(MusicServiceError):
        await svc.get_listenbrainz_token(service)  # type: ignore[arg-type]
    assert db.doc("music_services", LB_DOC)["auth"] == "username"  # type: ignore[index]


@pytest.mark.asyncio
async def test_oauth_state_extra_round_trips(db: FakeDb) -> None:
    svc = _service(db)
    state = await svc.create_oauth_state(USER, "listenbrainz", extra={"return_to": "app", "code_verifier": "v"})
    data = await svc.verify_oauth_state(state)
    assert data == {"user_id": USER, "service_type": "listenbrainz", "return_to": "app", "code_verifier": "v"}


def test_callback_after_oauth_switched_off_stores_nothing(make_client: Any, db: FakeDb) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    client2, _ = make_client(_settings(listenbrainz_oauth_enabled=False))
    # same db, new service instance; the state issued while on is still valid
    resp = _callback(client2, code="c", state=state)
    assert resp.headers["location"].endswith("reason=exchange_failed")
    assert db.doc("music_services", LB_DOC) is None
