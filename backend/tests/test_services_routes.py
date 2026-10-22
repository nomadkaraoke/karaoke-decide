"""Tests for services API routes."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import BackendSettings
from backend.services.sync_service import SyncResult
from karaoke_decide.core.exceptions import NotFoundError, ValidationError
from karaoke_decide.core.models import MusicService, User


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock backend settings."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret",
        frontend_url="http://localhost:3000",
        spotify_client_id="test-client-id",
        spotify_client_secret="test-client-secret",
        spotify_redirect_uri="http://localhost:8000/api/services/spotify/callback",
    )


@pytest.fixture
def sample_user() -> User:
    """Create a sample user."""
    now = datetime.now(UTC)
    return User(
        id="user_test123",
        email="test@example.com",
        display_name="Test User",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_spotify_service() -> MusicService:
    """Create sample Spotify service."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_test123_spotify",
        user_id="user_test123",
        service_type="spotify",
        service_user_id="spotify-456",
        service_username="SpotifyUser",
        access_token="valid-token",
        refresh_token="refresh-token",
        token_expires_at=now + timedelta(hours=1),
        last_sync_at=now - timedelta(hours=2),
        sync_status="idle",
        sync_error=None,
        tracks_synced=150,
        songs_synced=500,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_lastfm_service() -> MusicService:
    """Create sample Last.fm service."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_test123_lastfm",
        user_id="user_test123",
        service_type="lastfm",
        service_user_id="lastfmuser",
        service_username="LastFmUser",
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        last_sync_at=now - timedelta(days=1),
        sync_status="idle",
        sync_error=None,
        tracks_synced=200,
        songs_synced=1000,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_music_service_service(
    sample_spotify_service: MusicService,
    sample_lastfm_service: MusicService,
) -> MagicMock:
    """Create mock music service service."""
    mock = MagicMock()
    mock.get_user_services = AsyncMock(return_value=[sample_spotify_service, sample_lastfm_service])
    mock.get_service = AsyncMock(return_value=sample_spotify_service)
    mock.create_oauth_state = AsyncMock(return_value="test-oauth-state")
    mock.verify_oauth_state = AsyncMock(return_value={"user_id": "user_test123", "service_type": "spotify"})
    mock.get_spotify_auth_url = MagicMock(return_value="https://accounts.spotify.com/authorize?state=test")
    mock.create_or_update_spotify_service = AsyncMock(return_value=sample_spotify_service)
    mock.create_lastfm_service = AsyncMock(return_value=sample_lastfm_service)
    mock.delete_service = AsyncMock(return_value=None)

    # Mock spotify client on the service
    mock.spotify = MagicMock()
    mock.spotify.exchange_code = AsyncMock(
        return_value={
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
    )
    mock.spotify.get_current_user = AsyncMock(return_value={"id": "spotify-user", "display_name": "Test"})

    return mock


@pytest.fixture
def mock_sync_service() -> MagicMock:
    """Create mock sync service."""
    mock = MagicMock()
    mock.sync_all_services = AsyncMock(
        return_value=[
            SyncResult(
                service_type="spotify",
                tracks_fetched=100,
                tracks_matched=75,
                user_songs_created=50,
                user_songs_updated=25,
                error=None,
            ),
            SyncResult(
                service_type="lastfm",
                tracks_fetched=150,
                tracks_matched=100,
                user_songs_created=80,
                user_songs_updated=20,
                error=None,
            ),
        ]
    )
    return mock


@pytest.fixture
def mock_auth_service(sample_user: User) -> MagicMock:
    """Create mock auth service."""
    mock = MagicMock()
    mock.validate_jwt.return_value = {
        "sub": sample_user.id,
        "email": sample_user.email,
    }
    mock.get_user_by_id = AsyncMock(return_value=sample_user)
    return mock


@pytest.fixture
def mock_catalog_service() -> MagicMock:
    """Create mock catalog service."""
    mock = MagicMock()
    mock.search_songs.return_value = []
    return mock


@pytest.fixture
def mock_firestore_service() -> MagicMock:
    """Create mock firestore service."""
    mock = MagicMock()
    mock.set_document = AsyncMock(return_value=None)
    mock.get_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    # Mock query_documents for get_sync_status - returns empty list (no active jobs)
    mock.query_documents = AsyncMock(return_value=[])

    return mock


@pytest.fixture
def mock_cloud_tasks_service() -> MagicMock:
    """Create mock cloud tasks service."""
    mock = MagicMock()
    mock.create_sync_task = MagicMock(return_value="task-123")
    return mock


@pytest.fixture
def auth_client(
    mock_settings: BackendSettings,
    mock_music_service_service: MagicMock,
    mock_sync_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_catalog_service: MagicMock,
    mock_firestore_service: MagicMock,
    mock_cloud_tasks_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked services."""
    from backend.api import deps
    from backend.main import app

    # Use FastAPI's dependency override mechanism
    async def get_settings_override() -> BackendSettings:
        return mock_settings

    async def get_firestore_override() -> MagicMock:
        return mock_firestore_service

    app.dependency_overrides[deps.get_settings] = get_settings_override
    app.dependency_overrides[deps.get_firestore] = get_firestore_override

    with (
        patch("backend.api.deps.get_backend_settings", return_value=mock_settings),
        patch("backend.api.deps.get_music_service_service", return_value=mock_music_service_service),
        patch("backend.api.deps.get_sync_service", return_value=mock_sync_service),
        patch("backend.api.deps.get_auth_service", return_value=mock_auth_service),
        patch("backend.api.routes.catalog.get_catalog_service", return_value=mock_catalog_service),
        patch("backend.api.routes.services.get_cloud_tasks_service", return_value=mock_cloud_tasks_service),
    ):
        yield TestClient(app)

    # Clean up overrides
    app.dependency_overrides.clear()


class TestListServices:
    """Tests for GET /api/services."""

    def test_list_services_returns_connected(
        self,
        auth_client: TestClient,
    ) -> None:
        """Returns list of connected services."""
        response = auth_client.get(
            "/api/services",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["service_type"] == "spotify"
        assert data[1]["service_type"] == "lastfm"

    def test_list_services_requires_auth(self, auth_client: TestClient) -> None:
        """Returns 401 without authentication."""
        response = auth_client.get("/api/services")
        assert response.status_code == 401


class TestSpotifyConnect:
    """Tests for POST /api/services/spotify/connect."""

    def test_returns_auth_url(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Returns Spotify authorization URL."""
        response = auth_client.post(
            "/api/services/spotify/connect",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "auth_url" in data
        assert "accounts.spotify.com" in data["auth_url"]

    def test_creates_oauth_state(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Creates OAuth state for CSRF protection."""
        auth_client.post(
            "/api/services/spotify/connect",
            headers={"Authorization": "Bearer test-token"},
        )

        mock_music_service_service.create_oauth_state.assert_called_once()


class TestSpotifyCallback:
    """Tests for GET /api/services/spotify/callback."""

    def test_successful_callback_redirects(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Successful callback redirects to frontend success page."""
        response = auth_client.get(
            "/api/services/spotify/callback",
            params={"code": "auth-code", "state": "test-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "spotify/success" in response.headers["location"]

    def test_callback_with_error_redirects(
        self,
        auth_client: TestClient,
    ) -> None:
        """OAuth error redirects to frontend error page."""
        response = auth_client.get(
            "/api/services/spotify/callback",
            params={"error": "access_denied", "state": "test-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "spotify/error" in response.headers["location"]

    def test_callback_missing_params_redirects(
        self,
        auth_client: TestClient,
    ) -> None:
        """Missing parameters redirect to error page."""
        response = auth_client.get(
            "/api/services/spotify/callback",
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "error" in response.headers["location"]

    def test_callback_invalid_state_redirects(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Invalid state redirects to error page."""
        mock_music_service_service.verify_oauth_state = AsyncMock(return_value=None)

        response = auth_client.get(
            "/api/services/spotify/callback",
            params={"code": "auth-code", "state": "invalid-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "invalid_state" in response.headers["location"]


class TestLastFmConnect:
    """Tests for POST /api/services/lastfm/connect."""

    def test_connect_with_valid_username(
        self,
        auth_client: TestClient,
        sample_lastfm_service: MusicService,
    ) -> None:
        """Connects Last.fm with valid username."""
        response = auth_client.post(
            "/api/services/lastfm/connect",
            json={"username": "testuser"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["service_type"] == "lastfm"
        assert data["service_username"] == "LastFmUser"

    def test_connect_with_invalid_username(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Returns 422 for invalid username."""
        mock_music_service_service.create_lastfm_service = AsyncMock(
            side_effect=ValidationError("Invalid Last.fm username")
        )

        response = auth_client.post(
            "/api/services/lastfm/connect",
            json={"username": "invaliduser"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 422


class TestDisconnectService:
    """Tests for DELETE /api/services/{service_type}."""

    def test_disconnect_spotify(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Disconnects Spotify service."""
        response = auth_client.delete(
            "/api/services/spotify",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        assert "disconnected" in response.json()["message"].lower()
        mock_music_service_service.delete_service.assert_called_once()

    def test_disconnect_lastfm(
        self,
        auth_client: TestClient,
    ) -> None:
        """Disconnects Last.fm service."""
        response = auth_client.delete(
            "/api/services/lastfm",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200

    def test_disconnect_invalid_service(
        self,
        auth_client: TestClient,
    ) -> None:
        """Returns 400 for invalid service type."""
        response = auth_client.delete(
            "/api/services/invalid",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400

    def test_disconnect_not_connected(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
    ) -> None:
        """Returns 404 when service not connected."""
        mock_music_service_service.delete_service = AsyncMock(side_effect=NotFoundError("Service not connected"))

        response = auth_client.delete(
            "/api/services/spotify",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestSync:
    """Tests for POST /api/services/sync (async)."""

    def test_sync_starts_async_job(
        self,
        auth_client: TestClient,
        mock_firestore_service: MagicMock,
        mock_cloud_tasks_service: MagicMock,
    ) -> None:
        """Starts async sync job and returns job_id."""
        response = auth_client.post(
            "/api/services/sync",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert "message" in data

        # Verify job was stored in Firestore
        mock_firestore_service.set_document.assert_called_once()
        call_args = mock_firestore_service.set_document.call_args
        assert call_args[0][0] == "sync_jobs"  # collection name

        # Verify Cloud Task was created
        mock_cloud_tasks_service.create_sync_task.assert_called_once()

    def test_sync_requires_auth(self, auth_client: TestClient) -> None:
        """Returns 401 without authentication."""
        response = auth_client.post("/api/services/sync")
        assert response.status_code == 401


class TestSyncStatus:
    """Tests for GET /api/services/sync/status."""

    def test_get_sync_status(
        self,
        auth_client: TestClient,
    ) -> None:
        """Returns sync status for all services."""
        response = auth_client.get(
            "/api/services/sync/status",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "services" in data
        assert len(data["services"]) == 2

        # Check status fields
        for service in data["services"]:
            assert "sync_status" in service
            assert "tracks_synced" in service


class TestYouTubeMusicImport:
    """Tests for POST /api/services/youtube-music/import."""

    URL = "/api/services/youtube-music/import"
    AUTH = {"Authorization": "Bearer test-token"}

    def test_imports_playlist(self, auth_client: TestClient, mock_sync_service: MagicMock, sample_user: User) -> None:
        mock_sync_service.import_youtube_music_playlist = AsyncMock(
            return_value={
                "playlist_title": "My Likes",
                "tracks_fetched": 120,
                "tracks_matched": 45,
                "created": 120,
                "updated": 0,
            }
        )

        response = auth_client.post(self.URL, json={"playlist_url": "PLabc123"}, headers=self.AUTH)

        assert response.status_code == 200
        assert response.json() == {"playlist_title": "My Likes", "tracks_fetched": 120, "tracks_matched": 45}
        mock_sync_service.import_youtube_music_playlist.assert_awaited_once_with(
            sample_user.id, "PLabc123", is_guest=sample_user.is_guest
        )

    @pytest.mark.parametrize(
        ("error", "expected_status"),
        [
            ("InvalidPlaylistUrlError", 400),
            ("PrivatePlaylistError", 422),
            ("PlaylistNotFoundError", 404),
            ("PlaylistFetchError", 503),
            ("ImportRateLimitedError", 429),
        ],
    )
    def test_maps_errors_to_status_codes(
        self, auth_client: TestClient, mock_sync_service: MagicMock, error: str, expected_status: int
    ) -> None:
        from backend.services import sync_service
        from karaoke_decide.services import youtube_music

        error_cls = getattr(youtube_music, error, None) or getattr(sync_service, error)
        mock_sync_service.import_youtube_music_playlist = AsyncMock(side_effect=error_cls("x"))

        response = auth_client.post(self.URL, json={"playlist_url": "anything"}, headers=self.AUTH)

        assert response.status_code == expected_status

    def test_requires_auth(self, auth_client: TestClient) -> None:
        response = auth_client.post(self.URL, json={"playlist_url": "PLabc123"})
        assert response.status_code == 401

    def test_rejects_empty_url(self, auth_client: TestClient) -> None:
        response = auth_client.post(self.URL, json={"playlist_url": ""}, headers=self.AUTH)
        assert response.status_code == 422


class TestSpotifyPlaylistImport:
    """Tests for POST /api/services/spotify-playlist/import."""

    URL = "/api/services/spotify-playlist/import"
    AUTH = {"Authorization": "Bearer test-token"}

    def test_imports_playlist(self, auth_client: TestClient, mock_sync_service: MagicMock, sample_user: User) -> None:
        mock_sync_service.import_spotify_playlist = AsyncMock(
            return_value={"playlist_title": "Mix", "tracks_fetched": 50, "tracks_matched": 20, "songs_added": 50}
        )

        response = auth_client.post(self.URL, json={"url": "spotify:playlist:abc"}, headers=self.AUTH)

        assert response.status_code == 200
        assert response.json() == {"playlist_title": "Mix", "tracks_fetched": 50, "tracks_matched": 20}
        mock_sync_service.import_spotify_playlist.assert_awaited_once_with(sample_user.id, "spotify:playlist:abc")

    @pytest.mark.parametrize(
        ("error", "expected_status"),
        [
            ("InvalidSpotifyPlaylistUrlError", 400),
            ("SpotifyPlaylistNotFoundError", 404),
            ("SpotifyEmbedFormatChangedError", 422),
            ("ImportRateLimitedError", 429),
            ("SpotifyEmbedFetchError", 503),
        ],
    )
    def test_spotify_playlist_maps_errors(
        self, auth_client: TestClient, mock_sync_service: MagicMock, error: str, expected_status: int
    ) -> None:
        from backend.services import sync_service
        from karaoke_decide.services import spotify_embed

        error_cls = getattr(spotify_embed, error, None) or getattr(sync_service, error)
        mock_sync_service.import_spotify_playlist = AsyncMock(side_effect=error_cls("x"))

        response = auth_client.post(self.URL, json={"url": "anything"}, headers=self.AUTH)

        assert response.status_code == expected_status

    def test_requires_auth(self, auth_client: TestClient) -> None:
        assert auth_client.post(self.URL, json={"url": "x"}).status_code == 401


@pytest.fixture
def sample_apple_music_service() -> MusicService:
    """Create sample Apple Music service."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_test123_apple_music",
        user_id="user_test123",
        service_type="apple_music",
        service_user_id="us",
        service_username="Apple Music",
        access_token="music-user-token",
        refresh_token=None,
        token_expires_at=None,
        last_sync_at=None,
        sync_status="idle",
        sync_error=None,
        tracks_synced=0,
        created_at=now,
        updated_at=now,
    )


class TestAppleMusic:
    """Tests for the /api/services/apple-music/* routes."""

    def test_developer_token_is_public_and_origin_bound(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        mock_music_service_service.apple_music_signer.token = MagicMock(return_value=("dev-token", 1_900_000_000))

        response = auth_client.get("/api/services/apple-music/developer-token")

        assert response.status_code == 200
        assert response.json() == {"developer_token": "dev-token", "expires_at": 1_900_000_000}
        mock_music_service_service.apple_music_signer.token.assert_called_once_with(origins=("http://localhost:3000",))

    def test_developer_token_503_when_not_configured(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        from karaoke_decide.services.apple_music import AppleMusicNotConfiguredError

        mock_music_service_service.apple_music_signer.token = MagicMock(side_effect=AppleMusicNotConfiguredError("x"))

        assert auth_client.get("/api/services/apple-music/developer-token").status_code == 503

    def test_app_developer_token_needs_sign_in_and_has_no_origin(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        mock_music_service_service.apple_music_signer.token = MagicMock(return_value=("app-token", 1_900_000_000))

        assert auth_client.get("/api/services/apple-music/app-developer-token").status_code in (401, 403)
        response = auth_client.get(
            "/api/services/apple-music/app-developer-token", headers={"Authorization": "Bearer test-token"}
        )

        assert response.status_code == 200
        assert response.json() == {"developer_token": "app-token", "expires_at": 1_900_000_000}
        mock_music_service_service.apple_music_signer.token.assert_called_once_with()

    def test_link_returns_localized_connect_url(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        mock_music_service_service.STATE_EXPIRATION_MINUTES = 10

        response = auth_client.post(
            "/api/services/apple-music/link",
            headers={"Authorization": "Bearer test-token", "Accept-Language": "de"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "connect_url": "http://localhost:3000/de/connect/apple-music?state=test-oauth-state&return=app",
            "expires_in": 600,
        }
        mock_music_service_service.create_oauth_state.assert_called_once_with(
            "user_test123", "apple_music", account_hint="t•••@example.com"
        )

    def test_link_info_shows_the_masked_account(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        mock_music_service_service.peek_oauth_state = AsyncMock(
            return_value={"user_id": "u", "service_type": "apple_music", "account_hint": "t•••@example.com"}
        )

        response = auth_client.get("/api/services/apple-music/link-info", params={"state": "s"})

        assert response.status_code == 200
        assert response.json() == {"account": "t•••@example.com"}

    @pytest.mark.parametrize("link", [None, {"user_id": "u", "service_type": "spotify", "account_hint": None}])
    def test_link_info_404_for_expired_or_other_links(
        self, auth_client: TestClient, mock_music_service_service: MagicMock, link: dict | None
    ) -> None:
        mock_music_service_service.peek_oauth_state = AsyncMock(return_value=link)

        assert auth_client.get("/api/services/apple-music/link-info", params={"state": "s"}).status_code == 404

    def test_link_requires_auth(self, auth_client: TestClient) -> None:
        assert auth_client.post("/api/services/apple-music/link").status_code in (401, 403)

    def test_connect_signed_in_starts_sync(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
        mock_cloud_tasks_service: MagicMock,
        sample_apple_music_service: MusicService,
    ) -> None:
        mock_music_service_service.create_apple_music_service = AsyncMock(return_value=sample_apple_music_service)

        response = auth_client.post(
            "/api/services/apple-music/connect",
            json={"music_user_token": "mut"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["service"]["service_type"] == "apple_music"
        assert body["sync_job_id"]
        mock_music_service_service.create_apple_music_service.assert_called_once_with("user_test123", "mut")
        mock_cloud_tasks_service.create_sync_task.assert_called_once()
        assert mock_cloud_tasks_service.create_sync_task.call_args.kwargs["user_id"] == "user_test123"

    def test_connect_with_state_needs_no_sign_in(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
        sample_apple_music_service: MusicService,
    ) -> None:
        mock_music_service_service.peek_oauth_state = AsyncMock(
            return_value={"user_id": "user_from_link", "service_type": "apple_music", "account_hint": None}
        )
        mock_music_service_service.create_apple_music_service = AsyncMock(return_value=sample_apple_music_service)

        response = auth_client.post(
            "/api/services/apple-music/connect", json={"music_user_token": "mut", "state": "link-state"}
        )

        assert response.status_code == 200
        mock_music_service_service.create_apple_music_service.assert_called_once_with("user_from_link", "mut")
        mock_music_service_service.verify_oauth_state.assert_called_once_with("link-state")  # consumed

    def test_refused_token_keeps_the_link_for_a_retry(
        self, auth_client: TestClient, mock_music_service_service: MagicMock
    ) -> None:
        mock_music_service_service.peek_oauth_state = AsyncMock(
            return_value={"user_id": "user_from_link", "service_type": "apple_music", "account_hint": None}
        )
        mock_music_service_service.create_apple_music_service = AsyncMock(side_effect=ValidationError("refused"))

        response = auth_client.post(
            "/api/services/apple-music/connect", json={"music_user_token": "bad", "state": "link-state"}
        )

        assert response.status_code == 422
        mock_music_service_service.verify_oauth_state.assert_not_called()

    @pytest.mark.parametrize("link", [None, {"user_id": "u", "service_type": "spotify"}])
    def test_connect_rejects_bad_state(
        self, auth_client: TestClient, mock_music_service_service: MagicMock, link: dict | None
    ) -> None:
        mock_music_service_service.peek_oauth_state = AsyncMock(return_value=link)
        mock_music_service_service.create_apple_music_service = AsyncMock()

        response = auth_client.post("/api/services/apple-music/connect", json={"music_user_token": "m", "state": "s"})

        assert response.status_code == 401
        mock_music_service_service.create_apple_music_service.assert_not_called()

    def test_connect_without_auth_or_state_is_401(self, auth_client: TestClient) -> None:
        assert auth_client.post("/api/services/apple-music/connect", json={"music_user_token": "m"}).status_code == 401

    def test_connect_refused_token_is_422(self, auth_client: TestClient, mock_music_service_service: MagicMock) -> None:
        mock_music_service_service.create_apple_music_service = AsyncMock(side_effect=ValidationError("refused"))

        response = auth_client.post(
            "/api/services/apple-music/connect",
            json={"music_user_token": "bad"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 422

    def test_connect_still_succeeds_when_sync_cannot_start(
        self,
        auth_client: TestClient,
        mock_music_service_service: MagicMock,
        mock_cloud_tasks_service: MagicMock,
        sample_apple_music_service: MusicService,
    ) -> None:
        mock_music_service_service.create_apple_music_service = AsyncMock(return_value=sample_apple_music_service)
        mock_cloud_tasks_service.create_sync_task = MagicMock(side_effect=RuntimeError("queue down"))

        response = auth_client.post(
            "/api/services/apple-music/connect",
            json={"music_user_token": "mut"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        assert response.json()["sync_job_id"] is None

    def test_disconnect_apple_music(self, auth_client: TestClient, mock_music_service_service: MagicMock) -> None:
        response = auth_client.delete("/api/services/apple_music", headers={"Authorization": "Bearer test-token"})

        assert response.status_code == 200
        mock_music_service_service.delete_service.assert_called_once_with("user_test123", "apple_music")


@pytest.mark.parametrize(
    ("email", "masked"),
    [("andrew@example.com", "a•••@example.com"), ("x@y.z", "x•••@y.z"), (None, None), ("not-an-email", None)],
)
def test_mask_email(email: str | None, masked: str | None) -> None:
    from backend.api.routes.services import mask_email

    assert mask_email(email) == masked
