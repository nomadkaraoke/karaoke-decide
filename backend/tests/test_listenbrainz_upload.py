"""Proxying a Spotify export zip to ListenBrainz's import-listens on the user's behalf."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_music_service_service_dep, get_settings
from backend.api.routes.services import LISTENBRAINZ_UPLOAD_MAX_BYTES
from backend.services.music_service_service import ListenBrainzTokenUnavailableError, MusicServiceService
from backend.tests.test_listenbrainz_oauth import LB_DOC, USER, FakeDb, _settings
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import User
from karaoke_decide.services.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzImportInProgressError,
    ListenBrainzRateLimitedError,
)

ZIP = b"PK\x03\x04" + b"\x00" * 64
FILES = {"file": ("my_spotify_data.zip", ZIP, "application/zip")}
URL = "/api/services/listenbrainz/upload-export"


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def lb() -> MagicMock:
    client = MagicMock()
    client.submit_import = AsyncMock(return_value="42")
    client.get_import_status = AsyncMock(return_value={"status": "in_progress", "progress": "", "metadata": {}})
    return client


@pytest.fixture
def client(db: FakeDb, lb: MagicMock) -> Generator[TestClient, None, None]:
    from backend.main import app

    settings = _settings()
    svc = MusicServiceService(settings, db, listenbrainz_client=lb)  # type: ignore[arg-type]
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="fixture@example.com")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_music_service_service_dep] = lambda: svc
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


async def _connect(db: FakeDb, **over: Any) -> None:
    svc = MusicServiceService(_settings(), db)  # type: ignore[arg-type]
    await svc.create_listenbrainz_oauth_service(
        USER, {"access_token": "meba_a", "refresh_token": "meba_r", "expires_in": 3600}, "e2e-listener"
    )
    if over:
        await db.update_document("music_services", LB_DOC, over)


@pytest.mark.asyncio
async def test_upload_requires_oauth_409(client: TestClient, db: FakeDb) -> None:
    assert client.post(URL, files=FILES).json()["detail"] == "not_oauth_connected"  # not connected at all
    svc = MusicServiceService(
        _settings(),
        db,  # type: ignore[arg-type]
        listenbrainz_client=MagicMock(
            get_user_info=AsyncMock(return_value=MagicMock(username="e2e-listener", listen_count=1))
        ),
    )
    await svc.create_listenbrainz_service(USER, "e2e-listener")  # username-only connection
    resp = client.post(URL, files=FILES)
    assert resp.status_code == 409 and resp.json()["detail"] == "not_oauth_connected"


@pytest.mark.asyncio
async def test_upload_too_large_413(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    big = b"PK\x03\x04" + b"\x00" * LISTENBRAINZ_UPLOAD_MAX_BYTES
    resp = client.post(URL, files={"file": ("x.zip", big, "application/zip")})
    assert resp.status_code == 413 and resp.json()["detail"] == "too_large"
    lb.submit_import.assert_not_awaited()
    exact = b"PK\x03\x04" + b"\x00" * (LISTENBRAINZ_UPLOAD_MAX_BYTES - 4)
    assert client.post(URL, files={"file": ("x.zip", exact, "application/zip")}).status_code == 200


@pytest.mark.asyncio
async def test_upload_not_zip_400(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    resp = client.post(URL, files={"file": ("x.zip", b"%PDF-1.7 not a zip", "application/zip")})
    assert resp.status_code == 400 and resp.json()["detail"] == "not_a_zip"
    lb.submit_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_stores_pending_import(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    resp = client.post(URL, files=FILES)
    assert resp.status_code == 200
    assert resp.json() == {"import_id": "42", "status": "waiting"}
    lb.submit_import.assert_awaited_once_with("meba_a", ZIP, "my_spotify_data.zip")
    pending = db.doc("music_services", LB_DOC)["lb_pending_import"]  # type: ignore[index]
    assert pending["id"] == "42" and pending["status"] == "waiting" and pending["submitted_at"]


@pytest.mark.asyncio
async def test_second_upload_while_pending_409(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    assert client.post(URL, files=FILES).status_code == 200
    resp = client.post(URL, files=FILES)
    assert resp.status_code == 409 and resp.json()["detail"] == "import_in_progress"
    assert lb.submit_import.await_count == 1


@pytest.mark.asyncio
async def test_upload_allowed_once_pending_import_finished(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db, lb_pending_import={"id": "41", "status": "waiting", "submitted_at": "T"})
    lb.get_import_status.return_value = {"status": "completed", "progress": "", "metadata": {}}
    assert client.post(URL, files=FILES).status_code == 200
    assert db.doc("music_services", LB_DOC)["lb_pending_import"]["id"] == "42"  # type: ignore[index]


@pytest.mark.asyncio
async def test_lb_errors_map_to_503_409_429(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    lb.submit_import.side_effect = ExternalServiceError("ListenBrainz", "boom")
    assert client.post(URL, files=FILES).status_code == 503
    lb.submit_import.side_effect = ListenBrainzImportInProgressError()
    assert client.post(URL, files=FILES).json()["detail"] == "import_in_progress"
    lb.submit_import.side_effect = ListenBrainzRateLimitedError(12.4)
    resp = client.post(URL, files=FILES)
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "12"
    assert "lb_pending_import" not in db.doc("music_services", LB_DOC)  # type: ignore[operator]


# --- the client ---------------------------------------------------------------------------------


def _client_with(handler: Any) -> ListenBrainzClient:
    c = ListenBrainzClient()
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_client_submit_import_sends_multipart_and_token() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["Authorization"], req.read()
        return httpx.Response(200, json={"import_id": 7, "service": "spotify"})

    assert await _client_with(handler).submit_import("meba_a", ZIP, "e.zip") == "7"
    assert seen["url"] == "https://api.listenbrainz.org/1/import-listens/"
    assert seen["auth"] == "Token meba_a"
    assert b'name="file"; filename="e.zip"' in seen["body"]
    assert b'name="service"' in seen["body"] and b"spotify" in seen["body"]


@pytest.mark.asyncio
async def test_client_429_and_status() -> None:
    def limited(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-RateLimit-Reset-In": "9"})

    with pytest.raises(ListenBrainzRateLimitedError) as exc:
        await _client_with(limited).submit_import("t", ZIP, "e.zip")
    assert exc.value.retry_after == 9.0

    def status(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://api.listenbrainz.org/1/import-listens/7/"
        return httpx.Response(200, json={"import_id": 7, "metadata": {"status": "completed", "progress": "done"}})

    got = await _client_with(status).get_import_status("t", "7")
    assert got["status"] == "completed" and got["progress"] == "done"

    def busy(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 400, "error": "An import task is already in progress!"})

    with pytest.raises(ListenBrainzImportInProgressError):
        await _client_with(busy).submit_import("t", ZIP, "e.zip")


@pytest.mark.asyncio
async def test_status_check_error_does_not_block_upload(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(
        db, lb_pending_import={"id": "41", "status": "waiting", "submitted_at": datetime.now(UTC).isoformat()}
    )
    lb.get_import_status.side_effect = ExternalServiceError("ListenBrainz", "API error: 404")
    assert client.post(URL, files=FILES).status_code == 200
    lb.submit_import.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_pending_import_is_ignored(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    await _connect(db, lb_pending_import={"id": "41", "status": "waiting", "submitted_at": old})
    assert client.post(URL, files=FILES).status_code == 200
    lb.get_import_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_refresh_busy_is_retryable_503(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    await _connect(db)
    with patch.object(
        MusicServiceService,
        "get_listenbrainz_token",
        AsyncMock(side_effect=ListenBrainzTokenUnavailableError("busy")),
    ):
        resp = client.post(URL, files=FILES)
    assert resp.status_code == 503 and resp.json()["detail"] == "listenbrainz_unavailable"
    assert resp.headers["retry-after"] == "30"
    lb.submit_import.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_undecryptable_tokens_409_needs_reconnect(client: TestClient, db: FakeDb, lb: MagicMock) -> None:
    from backend.main import app

    await _connect(db)  # tokens encrypted with the default key
    rotated = _settings(decide_token_key="Hn2zDl8b0n8YJ2dZ9mH1vC5xQe3WkTt7uR4aPs6LfGo=")
    svc = MusicServiceService(rotated, db, listenbrainz_client=lb)  # type: ignore[arg-type]
    app.dependency_overrides[get_music_service_service_dep] = lambda: svc
    resp = client.post(URL, files=FILES)
    assert resp.status_code == 409 and resp.json()["detail"] == "needs_reconnect"
    stored = db.doc("music_services", LB_DOC)
    assert stored is not None and stored["error_code"] == "needs_reconnect" and stored["sync_status"] == "error"
    lb.submit_import.assert_not_awaited()
