"""Tests for health endpoints."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings


def test_health_check(client: TestClient) -> None:
    """Test health check endpoint returns healthy status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "karaoke-decide"


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock backend settings."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        cloud_tasks_location="us-central1",
        cloud_tasks_queue="sync-queue",
    )


@pytest.fixture
def mock_firestore_service() -> MagicMock:
    """Create mock firestore service."""
    mock = MagicMock()
    mock.count_documents = AsyncMock(return_value=42)
    return mock


@pytest.fixture
def deep_health_client(
    mock_settings: BackendSettings,
    mock_firestore_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked services for deep health check."""
    from backend.api import deps
    from backend.main import app

    async def get_settings_override() -> BackendSettings:
        return mock_settings

    async def get_firestore_override() -> MagicMock:
        return mock_firestore_service

    app.dependency_overrides[deps.get_settings] = get_settings_override
    app.dependency_overrides[deps.get_firestore] = get_firestore_override

    # Mock catalog service and cloud tasks
    mock_catalog = MagicMock()
    mock_catalog.get_catalog_stats.return_value = {"total_songs": 275000}

    mock_queue = MagicMock()
    mock_queue.name = "projects/test/locations/us-central1/queues/sync-queue"

    mock_tasks_client = MagicMock()
    mock_tasks_client.queue_path.return_value = mock_queue.name
    mock_tasks_client.get_queue.return_value = mock_queue

    with (
        patch("backend.api.routes.catalog.get_catalog_service", return_value=mock_catalog),
        patch("google.cloud.tasks_v2.CloudTasksClient", return_value=mock_tasks_client),
    ):
        yield TestClient(app)

    app.dependency_overrides.clear()


def test_deep_health_check_all_healthy(deep_health_client: TestClient) -> None:
    """Test deep health check returns healthy when all services are up."""
    response = deep_health_client.get("/api/health/deep")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "karaoke-decide"
    assert "timestamp" in data
    assert "checks" in data

    # Verify all checks passed
    assert data["checks"]["firestore"]["status"] == "healthy"
    assert data["checks"]["bigquery"]["status"] == "healthy"
    assert data["checks"]["cloud_tasks"]["status"] == "healthy"


def test_deep_health_check_degraded_on_failure(
    mock_settings: BackendSettings,
) -> None:
    """Test deep health check returns degraded when a service fails."""
    from backend.api import deps
    from backend.main import app

    # Create firestore that fails
    mock_firestore = MagicMock()
    mock_firestore.count_documents = AsyncMock(side_effect=Exception("Connection refused"))

    async def get_settings_override() -> BackendSettings:
        return mock_settings

    async def get_firestore_override() -> MagicMock:
        return mock_firestore

    app.dependency_overrides[deps.get_settings] = get_settings_override
    app.dependency_overrides[deps.get_firestore] = get_firestore_override

    mock_catalog = MagicMock()
    mock_catalog.get_catalog_stats.return_value = {"total_songs": 275000}

    mock_queue = MagicMock()
    mock_queue.name = "projects/test/locations/us-central1/queues/sync-queue"

    mock_tasks_client = MagicMock()
    mock_tasks_client.queue_path.return_value = mock_queue.name
    mock_tasks_client.get_queue.return_value = mock_queue

    with (
        patch("backend.api.routes.catalog.get_catalog_service", return_value=mock_catalog),
        patch("google.cloud.tasks_v2.CloudTasksClient", return_value=mock_tasks_client),
    ):
        client = TestClient(app)
        response = client.get("/api/health/deep")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["firestore"]["status"] == "unhealthy"
    assert "error" in data["checks"]["firestore"]
