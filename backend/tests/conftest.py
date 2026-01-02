"""Shared test fixtures for backend tests."""

import sys
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Mock google.cloud.tasks_v2 before any imports that need it
_mock_tasks_v2 = MagicMock()
_mock_tasks_v2.HttpMethod.POST = 1
sys.modules["google.cloud.tasks_v2"] = _mock_tasks_v2

import backend.api.deps  # noqa: E402, F401
import backend.api.routes.catalog  # noqa: E402, F401
from backend.config import BackendSettings  # noqa: E402
from backend.services.playlist_service import PlaylistInfo  # noqa: E402
from backend.services.quiz_service import QuizStatus, QuizSubmitResult  # noqa: E402
from karaoke_decide.core.models import QuizSong, Recommendation, User, UserSong  # noqa: E402


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

    # Mock update_user_profile
    mock.update_user_profile = AsyncMock(return_value=sample_user)

    # Mock firestore service (needed for verify endpoint to check guest upgrade)
    mock.firestore = mock_firestore_service
    mock.MAGIC_LINKS_COLLECTION = "magic_links"

    # Mock upgrade_guest_to_verified
    mock.upgrade_guest_to_verified = AsyncMock(return_value=sample_user)

    # Mock create_guest_user
    mock.create_guest_user = AsyncMock(return_value=sample_user)

    # Mock generate_guest_jwt
    mock.generate_guest_jwt = MagicMock(return_value=("test-guest-jwt-token", 2592000))

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


@pytest.fixture
def sample_quiz_songs() -> list[QuizSong]:
    """Sample quiz songs for testing."""
    return [
        QuizSong(
            id="1",
            artist="Queen",
            title="Bohemian Rhapsody",
            decade="1970s",
            popularity=85,
            brand_count=8,
        ),
        QuizSong(
            id="2",
            artist="Journey",
            title="Don't Stop Believin'",
            decade="1980s",
            popularity=80,
            brand_count=7,
        ),
        QuizSong(
            id="3",
            artist="Adele",
            title="Rolling in the Deep",
            decade="2010s",
            popularity=90,
            brand_count=6,
        ),
    ]


@pytest.fixture
def mock_quiz_service(sample_quiz_songs: list[QuizSong]) -> MagicMock:
    """Mock quiz service for API tests."""
    mock = MagicMock()
    mock.get_quiz_songs = AsyncMock(return_value=sample_quiz_songs)
    mock.submit_quiz = AsyncMock(return_value=QuizSubmitResult(songs_added=2, recommendations_ready=True))
    mock.get_quiz_status = AsyncMock(
        return_value=QuizStatus(
            completed=False,
            completed_at=None,
            songs_known_count=0,
        )
    )
    return mock


@pytest.fixture
def sample_user_songs(sample_user: User) -> list[UserSong]:
    """Sample user songs for testing."""
    return [
        UserSong(
            id=f"{sample_user.id}:1",
            user_id=sample_user.id,
            song_id="1",
            source="spotify",
            play_count=10,
            is_saved=True,
            times_sung=2,
            artist="Queen",
            title="Bohemian Rhapsody",
        ),
        UserSong(
            id=f"{sample_user.id}:2",
            user_id=sample_user.id,
            song_id="2",
            source="lastfm",
            play_count=5,
            is_saved=False,
            times_sung=1,
            artist="Journey",
            title="Don't Stop Believin'",
        ),
    ]


@pytest.fixture
def sample_recommendations() -> list[Recommendation]:
    """Sample recommendations for testing."""
    return [
        Recommendation(
            song_id="100",
            artist="Queen",
            title="We Will Rock You",
            score=0.85,
            reason="You listen to Queen",
            reason_type="known_artist",
            brand_count=8,
            popularity=80,
        ),
        Recommendation(
            song_id="101",
            artist="ABBA",
            title="Dancing Queen",
            score=0.70,
            reason="Popular karaoke song",
            reason_type="crowd_pleaser",
            brand_count=9,
            popularity=75,
        ),
    ]


@pytest.fixture
def mock_recommendation_service(
    sample_user_songs: list[UserSong],
    sample_recommendations: list[Recommendation],
) -> MagicMock:
    """Mock recommendation service for API tests."""
    mock = MagicMock()
    mock.get_user_songs = AsyncMock(return_value=(sample_user_songs, len(sample_user_songs)))
    mock.get_recommendations = AsyncMock(return_value=sample_recommendations)
    return mock


@pytest.fixture
def quiz_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_quiz_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked quiz service."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
        patch(
            "backend.api.deps.get_quiz_service",
            return_value=mock_quiz_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)


@pytest.fixture
def recommendations_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_recommendation_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked recommendation service."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
        patch(
            "backend.api.deps.get_recommendation_service",
            return_value=mock_recommendation_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)


@pytest.fixture
def sample_playlists(sample_user: User) -> list[PlaylistInfo]:
    """Sample playlists for testing."""
    return [
        PlaylistInfo(
            id="playlist-1",
            user_id=sample_user.id,
            name="Friday Night Karaoke",
            description="Songs for Friday night sessions",
            song_ids=["1", "2", "3"],
            song_count=3,
            created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        ),
        PlaylistInfo(
            id="playlist-2",
            user_id=sample_user.id,
            name="Crowd Pleasers",
            description="Always gets the crowd going",
            song_ids=["4", "5"],
            song_count=2,
            created_at=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
        ),
    ]


@pytest.fixture
def mock_playlist_service(sample_playlists: list[PlaylistInfo]) -> MagicMock:
    """Mock playlist service for API tests."""
    mock = MagicMock()
    mock.list_playlists = AsyncMock(return_value=sample_playlists)
    mock.create_playlist = AsyncMock(return_value=sample_playlists[0])
    mock.get_playlist = AsyncMock(return_value=sample_playlists[0])
    mock.update_playlist = AsyncMock(return_value=sample_playlists[0])
    mock.delete_playlist = AsyncMock(return_value=None)
    mock.add_song_to_playlist = AsyncMock(return_value=sample_playlists[0])
    mock.remove_song_from_playlist = AsyncMock(return_value=sample_playlists[0])
    return mock


@pytest.fixture
def playlist_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_playlist_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked playlist service."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
        patch(
            "backend.api.deps.get_playlist_service",
            return_value=mock_playlist_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)


@pytest.fixture
def mock_user_data_service() -> MagicMock:
    """Mock user data service for API tests."""
    mock = MagicMock()
    mock.get_data_summary = AsyncMock(
        return_value={
            "services": {
                "spotify": {"connected": True, "username": "testuser", "tracks_synced": 50},
                "lastfm": {"connected": False},
            },
            "artists": {"total": 10, "by_source": {"spotify": 5, "lastfm": 3, "quiz": 2, "manual": 0}},
            "songs": {"total": 100, "with_karaoke": 80},
            "preferences": {"completed": True, "decade": "1990s", "energy": "high", "genres": ["rock"]},
        }
    )
    mock.get_preferences = AsyncMock(
        return_value={
            "decade_preference": "1990s",
            "energy_preference": "high",
            "genres": ["rock", "pop"],
        }
    )
    mock.update_preferences = AsyncMock(
        return_value={
            "decade_preference": "2000s",
            "energy_preference": "medium",
            "genres": ["electronic"],
        }
    )
    mock.get_all_artists = AsyncMock(
        return_value=[
            {"artist_name": "Queen", "source": "spotify", "rank": 1, "time_range": "medium_term"},
            {"artist_name": "The Beatles", "source": "quiz", "rank": 1, "time_range": ""},
        ]
    )
    mock.add_artist = AsyncMock(
        return_value={
            "artists": ["Queen", "New Artist"],
            "added": "New Artist",
        }
    )
    mock.remove_artist = AsyncMock(
        return_value={
            "removed": "Queen",
            "removed_from": ["quiz"],
            "success": True,
        }
    )
    return mock


@pytest.fixture
def my_data_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_user_data_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked user data service."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
        patch(
            "backend.api.deps.get_user_data_service",
            return_value=mock_user_data_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)
