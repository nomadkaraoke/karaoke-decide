"""Shared test fixtures for backend tests."""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings
from karaoke_decide.core.models import User


@pytest.fixture
def mock_backend_settings() -> BackendSettings:
    """Create mock backend settings for testing."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
    )


@pytest.fixture
def auth_backend_settings() -> BackendSettings:
    """Create backend settings with JWT secret for auth testing."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        jwt_algorithm="HS256",
        jwt_expiration_hours=24,
        magic_link_expiration_minutes=15,
        frontend_url="http://localhost:3000",
    )


@pytest.fixture
def sample_user() -> User:
    """Create a sample user for testing."""
    return User(
        id="user_abc123def456",
        email="test@example.com",
        display_name="Test User",
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        total_songs_known=10,
        total_songs_sung=5,
        last_sync_at=None,
    )


@pytest.fixture
def mock_firestore_service() -> MagicMock:
    """Create a mock Firestore service for testing."""
    mock = MagicMock()
    mock.get_document = AsyncMock(return_value=None)
    mock.set_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    mock.delete_document = AsyncMock(return_value=None)
    mock.query_documents = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_email_service() -> MagicMock:
    """Create a mock email service for testing."""
    mock = MagicMock()
    mock.send_magic_link = AsyncMock(return_value=True)
    mock.is_configured = False  # Dev mode by default
    return mock


@pytest.fixture
def mock_auth_service(
    sample_user: User,
    mock_firestore_service: MagicMock,
    mock_email_service: MagicMock,
) -> MagicMock:
    """Create a mock auth service for API tests."""
    mock = MagicMock()

    # Mock send_magic_link
    mock.send_magic_link = AsyncMock(return_value=True)

    # Mock verify_magic_link
    mock.verify_magic_link = AsyncMock(return_value="test@example.com")

    # Mock get_or_create_user
    mock.get_or_create_user = AsyncMock(return_value=sample_user)

    # Mock get_user_by_id
    mock.get_user_by_id = AsyncMock(return_value=sample_user)

    # Mock generate_jwt
    mock.generate_jwt.return_value = ("test-jwt-token", 86400)

    # Mock validate_jwt
    mock.validate_jwt.return_value = {
        "sub": sample_user.id,
        "email": sample_user.email,
        "iat": 1704110400,
        "exp": 1704196800,
    }

    return mock


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


@pytest.fixture
def auth_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked auth and catalog services."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
    ):
        # Import app inside the patch context
        from backend.main import app

        yield TestClient(app)
