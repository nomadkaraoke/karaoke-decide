"""Tests for OIDC verification on /internal/* routes."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_settings
from backend.api.internal_auth import InternalCaller
from backend.config import BackendSettings

URL = "https://karaoke-decide-123.us-central1.run.app"
EMAIL = "123-compute@developer.gserviceaccount.com"
VERIFY = "backend.api.internal_auth.id_token.verify_oauth2_token"


def _client(settings: BackendSettings) -> TestClient:
    app = FastAPI()

    @app.post("/internal/ping", dependencies=[InternalCaller])
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _prod() -> BackendSettings:
    return BackendSettings(environment="production", google_cloud_project_number="123", cloud_run_url=URL)


def test_production_forged_cloudtasks_headers_403() -> None:
    with patch(VERIFY) as verify:
        r = _client(_prod()).post(
            "/internal/ping", headers={"X-CloudTasks-TaskName": "t", "X-CloudTasks-QueueName": "q"}
        )
    assert r.status_code == 403
    verify.assert_not_called()


def test_production_valid_token_ok() -> None:
    with patch(VERIFY, return_value={"email": EMAIL, "email_verified": True}) as verify:
        r = _client(_prod()).post("/internal/ping", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert verify.call_args.kwargs["audience"] == URL


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "attacker@example.com", "email_verified": True},
        {"email": EMAIL, "email_verified": False},
        {"email": EMAIL},
    ],
)
def test_wrong_email_or_unverified_403(claims: dict) -> None:
    with patch(VERIFY, return_value=claims):
        r = _client(_prod()).post("/internal/ping", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403


def test_wrong_audience_403() -> None:
    with patch(VERIFY, side_effect=ValueError("Token has wrong audience")):
        r = _client(_prod()).post("/internal/ping", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403


def test_production_without_cloud_run_url_403() -> None:
    s = BackendSettings(environment="production", google_cloud_project_number="123", cloud_run_url="")
    with patch(VERIFY, return_value={"email": EMAIL, "email_verified": True}):
        r = _client(s).post("/internal/ping", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403


def test_development_allows() -> None:
    assert _client(BackendSettings(environment="development")).post("/internal/ping").status_code == 200
