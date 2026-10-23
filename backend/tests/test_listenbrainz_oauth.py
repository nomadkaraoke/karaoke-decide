"""ListenBrainz connect via MetaBrainz OAuth, and service tokens encrypted at rest."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_music_service_service_dep, get_settings
from backend.config import BackendSettings
from backend.services import music_service_service as mss
from backend.services.music_service_service import (
    ListenBrainzTokenUnavailableError,
    MusicServiceError,
    MusicServiceService,
)
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
        self.writes = 0

    def doc(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.cols.get(collection, {}).get(doc_id)

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        d = self.doc(collection, doc_id)
        return copy.deepcopy(d) if d is not None else None

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.writes += 1
        self.cols.setdefault(collection, {})[doc_id] = copy.deepcopy(data)

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.writes += 1
        self.cols.setdefault(collection, {}).setdefault(doc_id, {}).update(copy.deepcopy(data))

    async def transform_document_atomically(self, collection: str, doc_id: str, transform: Any) -> Any:
        current = self.doc(collection, doc_id)
        new = transform(copy.deepcopy(current) if current is not None else None)  # may raise: no write
        self.writes += 1
        if new is None:
            self.cols.get(collection, {}).pop(doc_id, None)
        else:
            self.cols.setdefault(collection, {})[doc_id] = copy.deepcopy(new)
        return new

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


def _s256(verifier: str) -> str:
    """RFC 7636 S256: base64url(sha256(verifier)) without padding."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


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
    assert q["code_challenge"][0] == _s256(state_doc["extra"]["code_verifier"])


def test_start_rejects_unknown_return_to(make_client: Any) -> None:
    client, _ = make_client()
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": "evil"})
    assert resp.status_code == 422


# --- callback ---------------------------------------------------------------------------------


def test_callback_app_redirects_to_scheme(make_client: Any, db: FakeDb) -> None:
    client, svc = make_client()
    resp = client.get("/api/services/listenbrainz/oauth/start", params={"return_to": "app"})
    q = parse_qs(urlparse(resp.json()["auth_url"]).query)
    state = q["state"][0]
    state_doc = db.doc("oauth_states", state)
    assert state_doc is not None
    code_verifier = state_doc["extra"]["code_verifier"]
    assert q["code_challenge"] == [_s256(code_verifier)]  # the verifier sent at exchange matches the challenge

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
    resp = _callback(client, code="c", state="forged-state-0123456789")
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("listenbrainz=error&reason=invalid_state")
    svc.metabrainz.exchange_code.assert_not_awaited()


@pytest.mark.parametrize("state", ["short", "a/b/c-0123456789abcdef", "x" * 129, "bad state 0123456789"])
def test_callback_malformed_state_is_invalid_without_a_lookup(make_client: Any, state: str) -> None:
    client, svc = make_client()
    svc.verify_oauth_state = AsyncMock(side_effect=AssertionError("looked up"))
    resp = _callback(client, code="c", state=state)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("listenbrainz=error&reason=invalid_state")
    svc.verify_oauth_state.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_oauth_state_extra_cannot_override_identity(db: FakeDb) -> None:
    svc = _service(db)
    state = await svc.create_oauth_state(
        USER, "listenbrainz", extra={"user_id": "someone-else", "service_type": "spotify", "return_to": "web"}
    )
    data = await svc.verify_oauth_state(state)
    assert data is not None
    assert data["user_id"] == USER and data["service_type"] == "listenbrainz"
    assert data["return_to"] == "web"


def test_callback_after_oauth_switched_off_stores_nothing(make_client: Any, db: FakeDb) -> None:
    client, svc = make_client()
    state = _start(client, "app")
    client2, _ = make_client(_settings(listenbrainz_oauth_enabled=False))
    # same db, new service instance; the state issued while on is still valid
    resp = _callback(client2, code="c", state=state)
    assert resp.headers["location"].endswith("reason=exchange_failed")
    assert db.doc("music_services", LB_DOC) is None


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    refresh = cast(AsyncMock, svc.metabrainz.refresh)

    async def slow_refresh(_: str) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # the second caller arrives while the first is mid-refresh
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    refresh.side_effect = slow_refresh
    tokens = await asyncio.gather(*(svc.get_listenbrainz_token(service) for _ in range(3)))
    assert tokens == ["meba_access2"] * 3
    assert refresh.await_count == 1
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None and stored["refresh_lease_until"] is None


@pytest.mark.asyncio
async def test_refresh_uses_stored_refresh_token_not_a_stale_caller_copy(db: FakeDb) -> None:
    svc = _service(db)
    stale = await _lb_service(db, svc, 60)
    await svc.get_listenbrainz_token(stale)  # rotates r -> refresh2 and stores it
    await db.update_document("music_services", LB_DOC, {"token_expires_at": datetime.now(UTC).isoformat()})
    refresh = cast(AsyncMock, svc.metabrainz.refresh)
    await svc.get_listenbrainz_token(stale)  # `stale` still holds the old "meba_r"
    assert refresh.await_args is not None and refresh.await_args.args == ("meba_refresh2",)


@pytest.mark.asyncio
async def test_failed_write_after_refresh_marks_needs_reconnect(db: FakeDb, caplog: pytest.LogCaptureFixture) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    real_transform = db.transform_document_atomically

    async def failing_transform(collection: str, doc_id: str, transform: Any) -> Any:
        current = db.doc(collection, doc_id)
        if doc_id == LB_DOC and current is not None and current.get("refresh_lease_until"):
            probe = transform(copy.deepcopy(current))
            if probe and "access_token" in probe and probe["access_token"] != current["access_token"]:
                raise RuntimeError("firestore down")
        return await real_transform(collection, doc_id, transform)

    db.transform_document_atomically = failing_transform  # type: ignore[method-assign]
    with caplog.at_level("ERROR"), pytest.raises(MusicServiceError):
        await svc.get_listenbrainz_token(service)
    assert any(r.levelname == "ERROR" for r in caplog.records)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None
    assert stored["error_code"] == "needs_reconnect" and stored["refresh_lease_until"] is None


@pytest.mark.asyncio
async def test_waiters_issue_no_writes_while_lease_is_held(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    gate = asyncio.Event()

    async def slow_refresh(_: str) -> dict[str, Any]:
        await gate.wait()
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    svc.metabrainz.refresh.side_effect = slow_refresh  # type: ignore[attr-defined]
    holder = asyncio.create_task(svc.get_listenbrainz_token(service))
    await asyncio.sleep(0.05)
    writes_after_claim = db.writes
    waiters = [asyncio.create_task(svc.get_listenbrainz_token(service)) for _ in range(2)]
    await asyncio.sleep(0.8)  # several poll rounds
    assert db.writes == writes_after_claim
    gate.set()
    assert await asyncio.gather(holder, *waiters) == ["meba_access2"] * 3
    svc.metabrainz.refresh.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_transient_holder_failure_lets_a_waiter_refresh_once(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    calls = 0

    async def flaky(_: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        if calls == 1:
            raise ExternalServiceError("MetaBrainz", "503")
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    svc.metabrainz.refresh.side_effect = flaky  # type: ignore[attr-defined]
    first, second = await asyncio.gather(
        svc.get_listenbrainz_token(service), svc.get_listenbrainz_token(service), return_exceptions=True
    )
    results = [first, second]
    assert sorted(type(r).__name__ for r in results) == ["ListenBrainzTokenUnavailableError", "str"]
    # A later caller reads the token the waiter stored: exactly one retry refresh, no third.
    assert await svc.get_listenbrainz_token(service) == "meba_access2"
    assert calls == 2
    assert db.doc("music_services", LB_DOC)["refresh_lease_until"] is None  # type: ignore[index]


@pytest.mark.asyncio
@pytest.mark.parametrize("lease", ["2000-01-01T00:00:00+00:00", "not-a-date"])
async def test_expired_or_malformed_lease_is_reclaimable(db: FakeDb, lease: str) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    await db.update_document("music_services", LB_DOC, {"refresh_lease_until": lease})
    assert await svc.get_listenbrainz_token(service) == "meba_access2"


@pytest.mark.asyncio
async def test_needs_reconnect_doc_bails_without_refreshing(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    await db.update_document("music_services", LB_DOC, {"error_code": "needs_reconnect"})
    with pytest.raises(MusicServiceError):
        await svc.get_listenbrainz_token(service)
    svc.metabrainz.refresh.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lost_lease_does_not_clobber_and_marks_needs_reconnect(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)

    async def refresh_then_lose_lease(_: str) -> dict[str, Any]:
        await db.update_document("music_services", LB_DOC, {"refresh_lease_until": "2000-01-01T00:00:00+00:00"})
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    svc.metabrainz.refresh.side_effect = refresh_then_lose_lease  # type: ignore[attr-defined]
    with pytest.raises(MusicServiceError):
        await svc.get_listenbrainz_token(service)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None and stored["error_code"] == "needs_reconnect"
    assert stored["access_token"] != svc._cipher.encrypt("meba_access2")


@pytest.mark.asyncio
async def test_cancel_after_refresh_still_stores_tokens(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)

    async def slow(_: str) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    svc.metabrainz.refresh.side_effect = slow  # type: ignore[attr-defined]
    task = asyncio.create_task(svc.get_listenbrainz_token(service))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    reread = await svc.get_service(USER, "listenbrainz")
    assert reread is not None and reread.refresh_token == "meba_refresh2"


# --- review follow-ups: decrypt failure, reconnect, waiter timeout, task references ----------


@pytest.mark.asyncio
async def test_undecryptable_oauth_tokens_mark_needs_reconnect(db: FakeDb) -> None:
    await _lb_service(db, _service(db), 3600)  # encrypted with KEY
    rotated = _service(db, _settings(decide_token_key="Hn2zDl8b0n8YJ2dZ9mH1vC5xQe3WkTt7uR4aPs6LfGo="))
    service = await rotated.get_service(USER, "listenbrainz")
    assert service is not None and service.access_token is None
    with pytest.raises(MusicServiceError) as exc:
        await rotated.get_listenbrainz_token(service)
    assert not isinstance(exc.value, ListenBrainzTokenUnavailableError)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None and stored["error_code"] == "needs_reconnect" and stored["sync_status"] == "error"


@pytest.mark.asyncio
async def test_missing_key_does_not_mark_needs_reconnect(db: FakeDb) -> None:
    await _lb_service(db, _service(db), 3600)
    keyless = _service(db, _settings(decide_token_key=""))  # this process is misconfigured, not the connection
    service = await keyless.get_service(USER, "listenbrainz")
    with pytest.raises(MusicServiceError):
        await keyless.get_listenbrainz_token(service)  # type: ignore[arg-type]
    assert db.doc("music_services", LB_DOC)["error_code"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_reconnect_keeps_syncing_status_and_clears_lease(db: FakeDb) -> None:
    svc = _service(db)
    await _lb_service(db, svc, 3600)
    await db.update_document(
        "music_services",
        LB_DOC,
        {"sync_status": "syncing", "refresh_lease_until": "2999-01-01T00:00:00+00:00", "error_code": "needs_reconnect"},
    )
    await svc.create_listenbrainz_oauth_service(
        USER, {"access_token": "meba_new", "refresh_token": "meba_r2", "expires_in": 3600}, "e2e-listener"
    )
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None
    assert stored["sync_status"] == "syncing"
    assert stored["refresh_lease_until"] is None and stored["error_code"] is None


@pytest.mark.asyncio
async def test_reconnect_resets_error_status_to_idle(db: FakeDb) -> None:
    svc = _service(db)
    await _lb_service(db, svc, 3600)
    await db.update_document("music_services", LB_DOC, {"sync_status": "error", "error_code": "needs_reconnect"})
    await svc.create_listenbrainz_oauth_service(
        USER, {"access_token": "meba_new", "refresh_token": "meba_r2", "expires_in": 3600}, "e2e-listener"
    )
    assert db.doc("music_services", LB_DOC)["sync_status"] == "idle"  # type: ignore[index]


@pytest.mark.asyncio
async def test_reconnect_during_refresh_is_not_flagged_dead(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)

    async def refresh_while_user_reconnects(_: str) -> dict[str, Any]:
        await svc.create_listenbrainz_oauth_service(
            USER, {"access_token": "meba_new", "refresh_token": "meba_r2", "expires_in": 3600}, "e2e-listener"
        )  # clears our lease: the store below must fail, but the fresh connection must survive
        return {"access_token": "meba_access2", "refresh_token": "meba_refresh2", "expires_in": 3600}

    cast(AsyncMock, svc.metabrainz.refresh).side_effect = refresh_while_user_reconnects
    with pytest.raises(ListenBrainzTokenUnavailableError):
        await svc.get_listenbrainz_token(service)
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None and stored["error_code"] is None
    reread = await svc.get_service(USER, "listenbrainz")
    assert reread is not None and reread.refresh_token == "meba_r2"


@pytest.mark.asyncio
async def test_waiter_timeout_is_retryable_not_needs_reconnect(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    await db.update_document("music_services", LB_DOC, {"refresh_lease_until": "2999-01-01T00:00:00+00:00"})
    svc.REFRESH_WAIT_SECONDS = 0.3
    with pytest.raises(ListenBrainzTokenUnavailableError):
        await svc.get_listenbrainz_token(service)
    assert db.doc("music_services", LB_DOC)["error_code"] is None  # type: ignore[index]


def test_waiters_outlast_the_refresh_call() -> None:
    assert MusicServiceService.REFRESH_WAIT_SECONDS > MusicServiceService.REFRESH_CALL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_shielded_refresh_task_is_referenced_until_done(db: FakeDb) -> None:
    svc = _service(db)
    service = await _lb_service(db, svc, 60)
    gate = asyncio.Event()

    async def slow(_: str) -> dict[str, Any]:
        await gate.wait()
        raise ExternalServiceError("MetaBrainz", "503")

    cast(AsyncMock, svc.metabrainz.refresh).side_effect = slow
    caller = asyncio.create_task(svc.get_listenbrainz_token(service))
    await asyncio.sleep(0.05)
    assert len(mss._background_refreshes) == 1
    caller.cancel()  # the caller goes away; the refresh task must stay referenced
    await asyncio.sleep(0)
    assert len(mss._background_refreshes) == 1
    gate.set()
    await asyncio.sleep(0.05)
    assert not mss._background_refreshes  # done-callback dropped it (and retrieved its exception)
