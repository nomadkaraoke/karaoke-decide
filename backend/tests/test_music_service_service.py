"""Tests for music service service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.music_service_service import MusicServiceError, MusicServiceService
from karaoke_decide.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from karaoke_decide.core.models import MusicService


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock backend settings."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        spotify_client_id="test-client-id",
        spotify_client_secret="test-client-secret",
        spotify_redirect_uri="http://localhost:8000/api/services/spotify/callback",
        lastfm_api_key="test-lastfm-key",
        lastfm_shared_secret="test-lastfm-secret",
    )


@pytest.fixture
def mock_firestore() -> MagicMock:
    """Create mock Firestore service."""
    mock = MagicMock()
    mock.get_document = AsyncMock(return_value=None)
    mock.set_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    mock.delete_document = AsyncMock(return_value=None)
    mock.query_documents = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_spotify_client() -> MagicMock:
    """Create mock Spotify client."""
    mock = MagicMock()
    mock.get_auth_url = MagicMock(return_value="https://accounts.spotify.com/authorize?...")
    mock.exchange_code = AsyncMock(
        return_value={
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
        }
    )
    mock.refresh_token = AsyncMock(
        return_value={
            "access_token": "new-access-token",
            "expires_in": 3600,
        }
    )
    mock.get_current_user = AsyncMock(
        return_value={
            "id": "spotify-user-123",
            "display_name": "Test Spotify User",
        }
    )
    return mock


@pytest.fixture
def mock_lastfm_client() -> MagicMock:
    """Create mock Last.fm client."""
    mock = MagicMock()
    mock.get_user_info = AsyncMock(
        return_value={
            "user": {
                "name": "testlastfmuser",
                "playcount": "12345",
            }
        }
    )
    return mock


@pytest.fixture
def service(
    mock_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_spotify_client: MagicMock,
    mock_lastfm_client: MagicMock,
) -> MusicServiceService:
    """Create MusicServiceService with all mocks."""
    return MusicServiceService(
        settings=mock_settings,
        firestore=mock_firestore,
        spotify_client=mock_spotify_client,
        lastfm_client=mock_lastfm_client,
    )


@pytest.fixture
def sample_spotify_service() -> MusicService:
    """Create a sample Spotify service."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_123_spotify",
        user_id="user_123",
        service_type="spotify",
        service_user_id="spotify-user-456",
        service_username="Test User",
        access_token="valid-token",
        refresh_token="refresh-token",
        token_expires_at=now + timedelta(hours=1),
        last_sync_at=None,
        sync_status="idle",
        sync_error=None,
        tracks_synced=0,
        songs_synced=0,
        created_at=now,
        updated_at=now,
    )


class TestOAuthStateManagement:
    """Tests for OAuth state creation and verification."""

    @pytest.mark.asyncio
    async def test_create_oauth_state(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """OAuth state is created and stored."""
        state = await service.create_oauth_state("user_123", "spotify")

        assert len(state) > 20  # URL-safe token is reasonably long
        mock_firestore.set_document.assert_called_once()

        # Verify stored data
        call_args = mock_firestore.set_document.call_args
        assert call_args[0][0] == "oauth_states"  # Collection
        assert call_args[0][1] == state  # Document ID is the state
        stored_data = call_args[0][2]
        assert stored_data["user_id"] == "user_123"
        assert stored_data["service_type"] == "spotify"

    @pytest.mark.asyncio
    async def test_verify_oauth_state_valid(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Valid OAuth state returns user_id and service_type."""
        now = datetime.now(UTC)
        mock_firestore.delete_document_atomically = AsyncMock(
            return_value={
                "user_id": "user_123",
                "service_type": "spotify",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        )

        result = await service.verify_oauth_state("valid-state")

        assert result is not None
        assert result["user_id"] == "user_123"
        assert result["service_type"] == "spotify"
        # State should be atomically deleted during verification
        mock_firestore.delete_document_atomically.assert_called_once_with("oauth_states", "valid-state")

    @pytest.mark.asyncio
    async def test_verify_oauth_state_not_found(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Non-existent OAuth state returns None."""
        mock_firestore.delete_document_atomically = AsyncMock(return_value=None)

        result = await service.verify_oauth_state("invalid-state")

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_oauth_state_expired(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Expired OAuth state returns None (already deleted atomically)."""
        past = datetime.now(UTC) - timedelta(minutes=15)
        mock_firestore.delete_document_atomically = AsyncMock(
            return_value={
                "user_id": "user_123",
                "service_type": "spotify",
                "created_at": past.isoformat(),
                "expires_at": (past + timedelta(minutes=10)).isoformat(),
            }
        )

        result = await service.verify_oauth_state("expired-state")

        assert result is None
        # Document was already deleted atomically, even though expired
        mock_firestore.delete_document_atomically.assert_called_once()


class TestServiceCRUD:
    """Tests for service CRUD operations."""

    @pytest.mark.asyncio
    async def test_get_user_services_empty(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Returns empty list when no services connected."""
        mock_firestore.query_documents = AsyncMock(return_value=[])

        result = await service.get_user_services("user_123")

        assert result == []
        mock_firestore.query_documents.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_services_with_services(
        self, service: MusicServiceService, mock_firestore: MagicMock
    ) -> None:
        """Returns list of connected services."""
        now = datetime.now(UTC)
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "id": "user_123_spotify",
                    "user_id": "user_123",
                    "service_type": "spotify",
                    "service_user_id": "spotify-123",
                    "service_username": "SpotifyUser",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
                {
                    "id": "user_123_lastfm",
                    "user_id": "user_123",
                    "service_type": "lastfm",
                    "service_user_id": "lastfm-user",
                    "service_username": "LastFmUser",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            ]
        )

        result = await service.get_user_services("user_123")

        assert len(result) == 2
        assert result[0].service_type == "spotify"
        assert result[1].service_type == "lastfm"

    @pytest.mark.asyncio
    async def test_get_service_found(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Returns service when found."""
        now = datetime.now(UTC)
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": "user_123_spotify",
                "user_id": "user_123",
                "service_type": "spotify",
                "service_user_id": "spotify-123",
                "service_username": "SpotifyUser",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )

        result = await service.get_service("user_123", "spotify")

        assert result is not None
        assert result.service_type == "spotify"
        mock_firestore.get_document.assert_called_once_with("music_services", "user_123_spotify")

    @pytest.mark.asyncio
    async def test_get_service_not_found(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Returns None when service not found."""
        mock_firestore.get_document = AsyncMock(return_value=None)

        result = await service.get_service("user_123", "spotify")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_spotify_service_new(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Creates new Spotify service connection."""
        mock_firestore.get_document = AsyncMock(return_value=None)  # No existing service

        tokens = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }
        profile = {
            "id": "spotify-user-789",
            "display_name": "New User",
        }

        result = await service.create_or_update_spotify_service("user_123", tokens, profile)

        assert result.service_type == "spotify"
        assert result.service_user_id == "spotify-user-789"
        assert result.service_username == "New User"
        assert result.access_token == "new-access-token"
        mock_firestore.set_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_spotify_service_update_existing(
        self,
        service: MusicServiceService,
        mock_firestore: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Updates existing Spotify service connection."""
        # Mock existing service
        now = datetime.now(UTC)
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": sample_spotify_service.id,
                "user_id": sample_spotify_service.user_id,
                "service_type": "spotify",
                "service_user_id": sample_spotify_service.service_user_id,
                "service_username": sample_spotify_service.service_username,
                "access_token": "old-token",
                "refresh_token": sample_spotify_service.refresh_token,
                "token_expires_at": (now - timedelta(hours=1)).isoformat(),
                "created_at": sample_spotify_service.created_at.isoformat(),
                "updated_at": sample_spotify_service.updated_at.isoformat(),
            }
        )

        tokens = {
            "access_token": "updated-access-token",
            "expires_in": 3600,
            # No refresh_token - Spotify doesn't always return it
        }
        profile = {"id": "spotify-user", "display_name": "User"}

        result = await service.create_or_update_spotify_service("user_123", tokens, profile)

        assert result.access_token == "updated-access-token"
        # Refresh token should be preserved from existing
        assert result.refresh_token == sample_spotify_service.refresh_token
        mock_firestore.update_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_lastfm_service(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Creates Last.fm service connection."""
        mock_firestore.get_document = AsyncMock(return_value=None)  # No existing service

        result = await service.create_lastfm_service("user_123", "testlastfmuser")

        assert result.service_type == "lastfm"
        assert result.service_username == "testlastfmuser"
        assert result.access_token is None  # Last.fm doesn't use OAuth tokens
        mock_firestore.set_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_lastfm_service_invalid_user(
        self, service: MusicServiceService, mock_lastfm_client: MagicMock
    ) -> None:
        """Raises ValidationError for invalid Last.fm username."""
        mock_lastfm_client.get_user_info = AsyncMock(side_effect=ExternalServiceError("Last.fm", "User not found"))

        with pytest.raises(ValidationError, match="Invalid Last.fm username"):
            await service.create_lastfm_service("user_123", "nonexistentuser")

    @pytest.mark.asyncio
    async def test_delete_service(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Deletes service connection."""
        now = datetime.now(UTC)
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": "user_123_spotify",
                "user_id": "user_123",
                "service_type": "spotify",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )

        await service.delete_service("user_123", "spotify")

        mock_firestore.delete_document.assert_called_once_with("music_services", "user_123_spotify")

    @pytest.mark.asyncio
    async def test_delete_service_not_found(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Raises NotFoundError when deleting non-existent service."""
        mock_firestore.get_document = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await service.delete_service("user_123", "spotify")


class TestSyncStatus:
    """Tests for sync status updates."""

    @pytest.mark.asyncio
    async def test_update_sync_status_syncing(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Updates sync status to syncing."""
        await service.update_sync_status("user_123", "spotify", "syncing")

        mock_firestore.update_document.assert_called_once()
        call_args = mock_firestore.update_document.call_args
        assert call_args[0][2]["sync_status"] == "syncing"

    @pytest.mark.asyncio
    async def test_update_sync_status_error(self, service: MusicServiceService, mock_firestore: MagicMock) -> None:
        """Updates sync status to error with message."""
        await service.update_sync_status("user_123", "spotify", "error", error="Rate limited")

        call_args = mock_firestore.update_document.call_args
        assert call_args[0][2]["sync_status"] == "error"
        assert call_args[0][2]["sync_error"] == "Rate limited"

    @pytest.mark.asyncio
    async def test_update_sync_status_idle_with_count(
        self, service: MusicServiceService, mock_firestore: MagicMock
    ) -> None:
        """Updates sync status to idle with track count."""
        await service.update_sync_status("user_123", "spotify", "idle", tracks_synced=150)

        call_args = mock_firestore.update_document.call_args
        assert call_args[0][2]["sync_status"] == "idle"
        assert call_args[0][2]["tracks_synced"] == 150
        assert "last_sync_at" in call_args[0][2]


class TestTokenRefresh:
    """Tests for token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_token_when_expired(
        self,
        service: MusicServiceService,
        mock_firestore: MagicMock,
        mock_spotify_client: MagicMock,
    ) -> None:
        """Refreshes token when expired."""
        now = datetime.now(UTC)
        expired_service = MusicService(
            id="user_123_spotify",
            user_id="user_123",
            service_type="spotify",
            service_user_id="spotify-123",
            service_username="User",
            access_token="old-token",
            refresh_token="refresh-token",
            token_expires_at=now - timedelta(minutes=10),  # Expired
            created_at=now,
            updated_at=now,
        )

        result = await service.refresh_spotify_token_if_needed(expired_service)

        assert result.access_token == "new-access-token"
        mock_spotify_client.refresh_token.assert_called_once_with("refresh-token")
        mock_firestore.update_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_refresh_when_valid(
        self,
        service: MusicServiceService,
        mock_spotify_client: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Does not refresh token when still valid."""
        result = await service.refresh_spotify_token_if_needed(sample_spotify_service)

        assert result.access_token == sample_spotify_service.access_token
        mock_spotify_client.refresh_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_within_buffer(
        self,
        service: MusicServiceService,
        mock_firestore: MagicMock,
        mock_spotify_client: MagicMock,
    ) -> None:
        """Refreshes token when within buffer window."""
        now = datetime.now(UTC)
        expiring_soon = MusicService(
            id="user_123_spotify",
            user_id="user_123",
            service_type="spotify",
            service_user_id="spotify-123",
            service_username="User",
            access_token="old-token",
            refresh_token="refresh-token",
            token_expires_at=now + timedelta(minutes=2),  # Within 5 min buffer
            created_at=now,
            updated_at=now,
        )

        result = await service.refresh_spotify_token_if_needed(expiring_soon)

        assert result.access_token == "new-access-token"
        mock_spotify_client.refresh_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_fails_no_refresh_token(self, service: MusicServiceService) -> None:
        """Raises error when no refresh token available."""
        now = datetime.now(UTC)
        no_refresh = MusicService(
            id="user_123_spotify",
            user_id="user_123",
            service_type="spotify",
            service_user_id="spotify-123",
            service_username="User",
            access_token="old-token",
            refresh_token=None,  # No refresh token
            token_expires_at=now - timedelta(minutes=10),
            created_at=now,
            updated_at=now,
        )

        with pytest.raises(MusicServiceError, match="No refresh token"):
            await service.refresh_spotify_token_if_needed(no_refresh)

    @pytest.mark.asyncio
    async def test_refresh_fails_spotify_error(
        self, service: MusicServiceService, mock_spotify_client: MagicMock
    ) -> None:
        """Raises error when Spotify refresh fails."""
        now = datetime.now(UTC)
        expired_service = MusicService(
            id="user_123_spotify",
            user_id="user_123",
            service_type="spotify",
            service_user_id="spotify-123",
            service_username="User",
            access_token="old-token",
            refresh_token="invalid-refresh-token",
            token_expires_at=now - timedelta(minutes=10),
            created_at=now,
            updated_at=now,
        )

        mock_spotify_client.refresh_token = AsyncMock(
            side_effect=ExternalServiceError("Spotify", "Invalid refresh token")
        )

        with pytest.raises(MusicServiceError, match="Failed to refresh"):
            await service.refresh_spotify_token_if_needed(expired_service)

    @pytest.mark.asyncio
    async def test_get_valid_spotify_token(
        self,
        service: MusicServiceService,
        sample_spotify_service: MusicService,
    ) -> None:
        """Returns valid token without refresh when not expired."""
        token = await service.get_valid_spotify_token(sample_spotify_service)

        assert token == sample_spotify_service.access_token


class TestSpotifyAuthUrl:
    """Tests for Spotify auth URL generation."""

    def test_get_spotify_auth_url(self, service: MusicServiceService, mock_spotify_client: MagicMock) -> None:
        """Generates Spotify auth URL with state."""
        url = service.get_spotify_auth_url("test-state")

        mock_spotify_client.get_auth_url.assert_called_once_with("test-state")
        assert url == "https://accounts.spotify.com/authorize?..."
