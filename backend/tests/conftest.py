"""Shared test fixtures for backend tests."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings
from backend.main import app


@pytest.fixture
def client() -> TestClient:
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_backend_settings() -> BackendSettings:
    """Create mock backend settings for testing."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
    )


@pytest.fixture
def mock_firestore_client():
    """Mock Firestore async client."""
    with patch("google.cloud.firestore.AsyncClient") as mock:
        yield mock


@pytest.fixture
def mock_bigquery_client():
    """Mock BigQuery client for catalog tests."""
    with patch("karaoke_decide.services.bigquery_catalog.bigquery.Client") as mock:
        yield mock


@pytest.fixture
def sample_catalog_rows():
    """Sample catalog rows from BigQuery."""
    rows = []
    for i, (artist, title, brands) in enumerate([
        ("Queen", "Bohemian Rhapsody", "karafun,singa,lucky-voice,karaoke-version,karaoke-nerds"),
        ("Journey", "Don't Stop Believin'", "karafun,singa,lucky-voice,karaoke-version"),
        ("Adele", "Rolling in the Deep", "karafun,singa,lucky-voice"),
    ]):
        row = MagicMock()
        row.id = i + 1
        row.artist = artist
        row.title = title
        row.brands = brands
        row.brand_count = len(brands.split(","))
        rows.append(row)
    return rows
