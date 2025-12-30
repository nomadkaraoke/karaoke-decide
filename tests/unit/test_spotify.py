"""Tests for Spotify client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError, RateLimitError
from karaoke_decide.services.spotify import SpotifyClient


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for Spotify client."""
    settings = MagicMock(spec=Settings)
    settings.spotify_client_id = "test_client_id"
    settings.spotify_client_secret = "test_client_secret"
    settings.spotify_redirect_uri = "http://localhost:8000/callback"
    return settings


@pytest.fixture
def spotify_client(mock_settings: Settings) -> SpotifyClient:
    """Create SpotifyClient with mock settings."""
    return SpotifyClient(mock_settings)


class TestSpotifyClientInit:
    """Tests for SpotifyClient initialization."""

    def test_init_with_settings(self, mock_settings: Settings) -> None:
        """Test client initialization stores settings."""
        client = SpotifyClient(mock_settings)
        assert client.client_id == "test_client_id"
        assert client.client_secret == "test_client_secret"
        assert client.redirect_uri == "http://localhost:8000/callback"

    def test_auth_url_constant(self) -> None:
        """Test AUTH_URL constant is correct."""
        assert SpotifyClient.AUTH_URL == "https://accounts.spotify.com/authorize"

    def test_token_url_constant(self) -> None:
        """Test TOKEN_URL constant is correct."""
        assert SpotifyClient.TOKEN_URL == "https://accounts.spotify.com/api/token"

    def test_api_base_constant(self) -> None:
        """Test API_BASE constant is correct."""
        assert SpotifyClient.API_BASE == "https://api.spotify.com/v1"

    def test_scopes_defined(self) -> None:
        """Test required scopes are defined."""
        assert "user-library-read" in SpotifyClient.SCOPES
        assert "user-top-read" in SpotifyClient.SCOPES
        assert "user-read-recently-played" in SpotifyClient.SCOPES


class TestGetAuthUrl:
    """Tests for get_auth_url method."""

    def test_generates_auth_url(self, spotify_client: SpotifyClient) -> None:
        """Test auth URL generation."""
        url = spotify_client.get_auth_url("test_state")

        assert SpotifyClient.AUTH_URL in url
        assert "client_id=test_client_id" in url
        assert "response_type=code" in url
        assert "state=test_state" in url

    def test_includes_scopes(self, spotify_client: SpotifyClient) -> None:
        """Test auth URL includes scopes."""
        url = spotify_client.get_auth_url("state")
        assert "scope=" in url

    def test_includes_redirect_uri(self, spotify_client: SpotifyClient) -> None:
        """Test auth URL includes redirect URI."""
        url = spotify_client.get_auth_url("state")
        assert "redirect_uri=" in url


class TestExchangeCode:
    """Tests for exchange_code method."""

    @pytest.mark.asyncio
    async def test_exchange_code_success(self, spotify_client: SpotifyClient) -> None:
        """Test successful code exchange."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await spotify_client.exchange_code("auth_code")

            assert result["access_token"] == "access123"
            assert result["refresh_token"] == "refresh456"

    @pytest.mark.asyncio
    async def test_exchange_code_failure(self, spotify_client: SpotifyClient) -> None:
        """Test code exchange failure raises error."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid code"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError) as exc_info:
                await spotify_client.exchange_code("bad_code")

            assert "Token exchange failed" in str(exc_info.value)


class TestRefreshToken:
    """Tests for refresh_token method."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, spotify_client: SpotifyClient) -> None:
        """Test successful token refresh."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            result = await spotify_client.refresh_token("refresh_token")

            assert result["access_token"] == "new_access_token"

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, spotify_client: SpotifyClient) -> None:
        """Test token refresh failure raises error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid refresh token"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError):
                await spotify_client.refresh_token("bad_token")


class TestApiRequest:
    """Tests for _api_request method."""

    @pytest.mark.asyncio
    async def test_api_request_success(self, spotify_client: SpotifyClient) -> None:
        """Test successful API request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "user123", "display_name": "Test"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_response
            )

            result = await spotify_client._api_request("GET", "/me", "access_token")

            assert result["id"] == "user123"

    @pytest.mark.asyncio
    async def test_api_request_rate_limited(
        self, spotify_client: SpotifyClient
    ) -> None:
        """Test rate limit handling."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "30"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(RateLimitError) as exc_info:
                await spotify_client._api_request("GET", "/me", "token")

            assert "Rate limited" in str(exc_info.value)
            assert "30" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_api_request_error(self, spotify_client: SpotifyClient) -> None:
        """Test API error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError):
                await spotify_client._api_request("GET", "/me", "token")


class TestSpotifyApiMethods:
    """Tests for high-level API methods."""

    @pytest.mark.asyncio
    async def test_get_current_user(self, spotify_client: SpotifyClient) -> None:
        """Test get_current_user calls correct endpoint."""
        with patch.object(
            spotify_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"id": "user123"}

            result = await spotify_client.get_current_user("token")

            mock_request.assert_called_once_with("GET", "/me", "token")
            assert result["id"] == "user123"

    @pytest.mark.asyncio
    async def test_get_saved_tracks(self, spotify_client: SpotifyClient) -> None:
        """Test get_saved_tracks calls correct endpoint."""
        with patch.object(
            spotify_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"items": []}

            await spotify_client.get_saved_tracks("token", limit=20, offset=10)

            mock_request.assert_called_once_with(
                "GET", "/me/tracks", "token", params={"limit": 20, "offset": 10}
            )

    @pytest.mark.asyncio
    async def test_get_top_tracks(self, spotify_client: SpotifyClient) -> None:
        """Test get_top_tracks calls correct endpoint."""
        with patch.object(
            spotify_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"items": []}

            await spotify_client.get_top_tracks(
                "token", time_range="short_term", limit=10
            )

            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["params"]["time_range"] == "short_term"

    @pytest.mark.asyncio
    async def test_get_recently_played(self, spotify_client: SpotifyClient) -> None:
        """Test get_recently_played calls correct endpoint."""
        with patch.object(
            spotify_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"items": []}

            await spotify_client.get_recently_played("token", limit=25)

            mock_request.assert_called_once_with(
                "GET", "/me/player/recently-played", "token", params={"limit": 25}
            )
