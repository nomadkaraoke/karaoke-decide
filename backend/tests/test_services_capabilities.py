"""Tests for GET /api/services/capabilities and the 3b settings."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_settings
from backend.config import BackendSettings


@pytest.fixture
def cap_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    from backend.main import app

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)


def _use(client: TestClient, settings: BackendSettings) -> None:
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]


def test_capabilities_default_off(cap_client: TestClient) -> None:
    _use(cap_client, BackendSettings())
    resp = cap_client.get("/api/services/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {
        "screenshot_import": False,
        "listenbrainz_oauth": False,
        "listenbrainz_upload_max_bytes": 31457280,
    }


def test_capabilities_on(cap_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _use(
        cap_client,
        BackendSettings(
            listenbrainz_oauth_enabled=True,
            metabrainz_client_id="id",
            metabrainz_client_secret="secret",
            decide_token_key="key",
        ),
    )
    body = cap_client.get("/api/services/capabilities").json()
    assert body["screenshot_import"] is True
    assert body["listenbrainz_oauth"] is True


def test_oauth_needs_all_four(cap_client: TestClient) -> None:
    _use(
        cap_client,
        BackendSettings(listenbrainz_oauth_enabled=True, metabrainz_client_id="id", metabrainz_client_secret="s"),
    )
    assert cap_client.get("/api/services/capabilities").json()["listenbrainz_oauth"] is False


def test_fake_externals_enable_screenshot(cap_client: TestClient) -> None:
    _use(cap_client, BackendSettings(decide_fake_externals=True))
    assert cap_client.get("/api/services/capabilities").json()["screenshot_import"] is True


def test_settings_defaults_and_internal_invoker() -> None:
    s = BackendSettings(google_cloud_project_number="123")
    assert s.screenshot_llm_model == "gemini-3.8-flash"
    assert s.lb_poll_budget_seconds == 1500
    assert s.public_api_base_url == "https://decide.nomadkaraoke.com"
    assert s.mobile_app_scheme == "nomadkaraoke"
    assert s.inline_tasks is False
    assert s.internal_invoker_emails == {"123-compute@developer.gserviceaccount.com"}
