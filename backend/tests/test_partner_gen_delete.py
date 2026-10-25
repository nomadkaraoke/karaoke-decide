"""DELETE /api/partner/gen/users: gen's account deletion erases everything decide holds."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api.deps import get_firestore, get_settings
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.config import BackendSettings
from backend.services.voice_recording_service import LocalBucket
from backend.tests.test_test_support import FakeFirestore

SECRET = "gen-partner-secret"
EMAIL = "Maestro@Example.com"
USER_ID = "user_maestro"
URL = "/api/partner/gen/users"


def _client(monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore, tmp_path: Path, secret: str) -> TestClient:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        decide_gen_partner_secret=secret,
    )
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    c = TestClient(main.create_app())
    c.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    c.app.dependency_overrides[get_firestore] = lambda: fs  # type: ignore[attr-defined]
    c.app.dependency_overrides[get_recordings_bucket] = lambda: LocalBucket(tmp_path / "rec")  # type: ignore[attr-defined]
    return c


@pytest.fixture
def fs() -> FakeFirestore:
    fs = FakeFirestore()
    fs.cols["decide_users"] = {
        hashlib.sha256(EMAIL.lower().encode()).hexdigest(): {"user_id": USER_ID, "email": EMAIL.lower()}
    }
    fs.cols["user_songs"] = {f"{USER_ID}:s1": {"user_id": USER_ID}, "other:s1": {"user_id": "other"}}
    return fs


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore, tmp_path: Path) -> Iterator[TestClient]:
    c = _client(monkeypatch, fs, tmp_path, SECRET)
    yield c
    c.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _delete(c: TestClient, headers: dict[str, str] | None = None, body: Any = None) -> Any:
    return c.request("DELETE", URL, json={"email": EMAIL} if body is None else body, headers=headers or {})


def test_requires_secret(client: TestClient, fs: FakeFirestore) -> None:
    assert _delete(client).status_code == 401
    assert _delete(client, {"X-Partner-Secret": "wrong"}).status_code == 401
    assert _delete(client, {"X-Partner-Secret": ""}).status_code == 401
    assert "user_songs" in fs.cols and f"{USER_ID}:s1" in fs.cols["user_songs"]  # nothing erased


def test_unset_secret_503(monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore, tmp_path: Path) -> None:
    c = _client(monkeypatch, fs, tmp_path, "")
    assert _delete(c, {"X-Partner-Secret": ""}).status_code == 503
    assert _delete(c, {"X-Partner-Secret": SECRET}).status_code == 503
    assert f"{USER_ID}:s1" in fs.cols["user_songs"]


def test_deletes_and_returns_counts(client: TestClient, fs: FakeFirestore) -> None:
    r = _delete(client, {"X-Partner-Secret": SECRET})
    assert r.status_code == 200
    deleted = r.json()["deleted"]
    assert deleted["user_songs"] == 1
    assert deleted["decide_users"] == 1
    assert list(fs.cols["user_songs"]) == ["other:s1"]  # another user's row untouched
    assert fs.cols["decide_users"] == {}


def test_second_call_empty_counts(client: TestClient) -> None:
    assert _delete(client, {"X-Partner-Secret": SECRET}).status_code == 200
    r = _delete(client, {"X-Partner-Secret": SECRET})
    assert r.status_code == 200
    assert r.json() == {"deleted": {}}


def test_missing_email_is_422(client: TestClient) -> None:
    assert _delete(client, {"X-Partner-Secret": SECRET}, body={}).status_code == 422
