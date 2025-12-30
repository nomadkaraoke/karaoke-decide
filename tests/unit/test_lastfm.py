"""Tests for Last.fm client."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.lastfm import LastFmClient


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for Last.fm client."""
    settings = MagicMock(spec=Settings)
    settings.lastfm_api_key = "test_api_key"
    settings.lastfm_shared_secret = "test_shared_secret"
    return settings


@pytest.fixture
def lastfm_client(mock_settings: Settings) -> LastFmClient:
    """Create LastFmClient with mock settings."""
    return LastFmClient(mock_settings)


class TestLastFmClientInit:
    """Tests for LastFmClient initialization."""

    def test_init_with_settings(self, mock_settings: Settings) -> None:
        """Test client initialization stores settings."""
        client = LastFmClient(mock_settings)
        assert client.api_key == "test_api_key"
        assert client.shared_secret == "test_shared_secret"

    def test_api_base_constant(self) -> None:
        """Test API_BASE constant is correct."""
        assert LastFmClient.API_BASE == "https://ws.audioscrobbler.com/2.0/"


class TestGenerateSignature:
    """Tests for _generate_signature method."""

    def test_generates_md5_signature(self, lastfm_client: LastFmClient) -> None:
        """Test signature generation creates MD5 hash."""
        params = {"method": "user.getinfo", "user": "testuser", "api_key": "key"}
        signature = lastfm_client._generate_signature(params)

        # Signature should be 32-char hex string (MD5)
        assert len(signature) == 32
        assert all(c in "0123456789abcdef" for c in signature)

    def test_signature_order_independent(self, lastfm_client: LastFmClient) -> None:
        """Test signature is the same regardless of param order."""
        params1 = {"a": "1", "b": "2", "c": "3"}
        params2 = {"c": "3", "a": "1", "b": "2"}

        sig1 = lastfm_client._generate_signature(params1)
        sig2 = lastfm_client._generate_signature(params2)

        assert sig1 == sig2

    def test_signature_includes_secret(self, lastfm_client: LastFmClient) -> None:
        """Test signature includes shared secret."""
        params = {"test": "value"}
        signature = lastfm_client._generate_signature(params)

        # Manually compute expected signature
        expected = hashlib.md5(b"testvaluetest_shared_secret").hexdigest()
        assert signature == expected


class TestApiRequest:
    """Tests for _api_request method."""

    @pytest.mark.asyncio
    async def test_api_request_success(self, lastfm_client: LastFmClient) -> None:
        """Test successful API request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user": {"name": "testuser", "playcount": "1000"}
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await lastfm_client._api_request(
                "user.getinfo", {"user": "testuser"}
            )

            assert result["user"]["name"] == "testuser"

    @pytest.mark.asyncio
    async def test_api_request_http_error(self, lastfm_client: LastFmClient) -> None:
        """Test HTTP error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError) as exc_info:
                await lastfm_client._api_request("user.getinfo", {"user": "test"})

            assert "Last.fm" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_api_request_lastfm_error(self, lastfm_client: LastFmClient) -> None:
        """Test Last.fm error response handling."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "error": 6,
            "message": "User not found",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError) as exc_info:
                await lastfm_client._api_request("user.getinfo", {"user": "bad_user"})

            assert "User not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_api_request_includes_api_key(
        self, lastfm_client: LastFmClient
    ) -> None:
        """Test API requests include api_key parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        with patch("httpx.AsyncClient") as mock_client:
            mock_get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.get = mock_get

            await lastfm_client._api_request("user.getinfo", {"user": "test"})

            call_args = mock_get.call_args
            params = call_args[1]["params"]
            assert params["api_key"] == "test_api_key"
            assert params["format"] == "json"


class TestLastFmApiMethods:
    """Tests for high-level API methods."""

    @pytest.mark.asyncio
    async def test_get_user_info(self, lastfm_client: LastFmClient) -> None:
        """Test get_user_info calls correct method."""
        with patch.object(
            lastfm_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"user": {"name": "test"}}

            result = await lastfm_client.get_user_info("testuser")

            mock_request.assert_called_once_with(
                "user.getinfo", {"user": "testuser"}
            )
            assert result["user"]["name"] == "test"

    @pytest.mark.asyncio
    async def test_get_loved_tracks(self, lastfm_client: LastFmClient) -> None:
        """Test get_loved_tracks calls correct method."""
        with patch.object(
            lastfm_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"lovedtracks": {"track": []}}

            await lastfm_client.get_loved_tracks("user", limit=25, page=2)

            mock_request.assert_called_once_with(
                "user.getlovedtracks", {"user": "user", "limit": 25, "page": 2}
            )

    @pytest.mark.asyncio
    async def test_get_top_tracks(self, lastfm_client: LastFmClient) -> None:
        """Test get_top_tracks calls correct method."""
        with patch.object(
            lastfm_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"toptracks": {"track": []}}

            await lastfm_client.get_top_tracks(
                "user", period="7day", limit=10, page=1
            )

            mock_request.assert_called_once_with(
                "user.gettoptracks",
                {"user": "user", "period": "7day", "limit": 10, "page": 1},
            )

    @pytest.mark.asyncio
    async def test_get_recent_tracks(self, lastfm_client: LastFmClient) -> None:
        """Test get_recent_tracks calls correct method."""
        with patch.object(
            lastfm_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"recenttracks": {"track": []}}

            await lastfm_client.get_recent_tracks("user", limit=30, page=1)

            call_args = mock_request.call_args
            assert call_args[0][0] == "user.getrecenttracks"
            assert call_args[0][1]["extended"] == 1

    @pytest.mark.asyncio
    async def test_get_track_info(self, lastfm_client: LastFmClient) -> None:
        """Test get_track_info calls correct method."""
        with patch.object(
            lastfm_client, "_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"track": {"name": "Song"}}

            await lastfm_client.get_track_info("Artist", "Song Title")

            mock_request.assert_called_once_with(
                "track.getInfo", {"artist": "Artist", "track": "Song Title"}
            )
