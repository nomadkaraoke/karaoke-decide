"""Shared test fixtures for backend tests."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings


@pytest.fixture
def mock_backend_settings() -> BackendSettings:
    """Create mock backend settings for testing."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
    )


@pytest.fixture
def mock_firestore_client() -> Generator[MagicMock, None, None]:
    """Mock Firestore async client."""
    with patch("google.cloud.firestore.AsyncClient") as mock:
        yield mock


@pytest.fixture
def mock_bigquery_client() -> Generator[MagicMock, None, None]:
    """Mock BigQuery client for catalog tests."""
    with patch("karaoke_decide.services.bigquery_catalog.bigquery.Client") as mock:
        yield mock


@pytest.fixture
def sample_catalog_rows() -> list[MagicMock]:
    """Sample catalog rows from BigQuery."""
    rows: list[MagicMock] = []
    for i, (artist, title, brands) in enumerate(
        [
            (
                "Queen",
                "Bohemian Rhapsody",
                "karafun,singa,lucky-voice,karaoke-version,karaoke-nerds",
            ),
            (
                "Journey",
                "Don't Stop Believin'",
                "karafun,singa,lucky-voice,karaoke-version",
            ),
            ("Adele", "Rolling in the Deep", "karafun,singa,lucky-voice"),
        ]
    ):
        row = MagicMock()
        row.id = i + 1
        row.artist = artist
        row.title = title
        row.brands = brands
        row.brand_count = len(brands.split(","))
        rows.append(row)
    return rows


@pytest.fixture
def mock_catalog_service(sample_catalog_rows: list[MagicMock]) -> MagicMock:
    """Mock the catalog service for API tests."""
    mock_service = MagicMock()

    # Mock search_songs
    mock_service.search_songs.return_value = sample_catalog_rows

    # Mock get_songs_by_artist
    mock_service.get_songs_by_artist.return_value = [sample_catalog_rows[0]]

    # Mock get_popular_songs
    mock_service.get_popular_songs.return_value = sample_catalog_rows

    # Mock get_song_by_id
    def get_song_by_id(song_id: int) -> MagicMock | None:
        for row in sample_catalog_rows:
            if row.id == song_id:
                return row
        return None

    mock_service.get_song_by_id.side_effect = get_song_by_id

    # Mock get_stats
    mock_service.get_stats.return_value = {
        "total_songs": 275809,
        "unique_artists": 50000,
        "max_brand_count": 10,
        "avg_brand_count": 2.5,
    }

    return mock_service


@pytest.fixture
def client(mock_catalog_service: MagicMock) -> Generator[TestClient, None, None]:
    """Create test client with mocked catalog service."""
    with patch(
        "backend.api.routes.catalog.get_catalog_service",
        return_value=mock_catalog_service,
    ):
        # Import app inside the patch context
        from backend.main import app

        yield TestClient(app)
