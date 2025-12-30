"""Tests for KaraokeNerds client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import KaraokeSong
from karaoke_decide.services.karaokenerds import KaraokeNerdsClient


@pytest.fixture
def karaokenerds_client() -> KaraokeNerdsClient:
    """Create KaraokeNerdsClient instance."""
    return KaraokeNerdsClient()


class TestKaraokeNerdsClientInit:
    """Tests for KaraokeNerdsClient initialization."""

    def test_catalog_url_constant(self) -> None:
        """Test CATALOG_URL constant is defined."""
        assert KaraokeNerdsClient.CATALOG_URL == "https://karaokenerds.com/api/songs"


class TestFetchCatalog:
    """Tests for fetch_catalog method."""

    @pytest.mark.asyncio
    async def test_fetch_catalog_success(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test successful catalog fetch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 1, "artist": "Queen", "title": "Bohemian Rhapsody"},
            {"id": 2, "artist": "Journey", "title": "Don't Stop Believin'"},
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await karaokenerds_client.fetch_catalog()

            assert len(result) == 2
            assert result[0]["artist"] == "Queen"

    @pytest.mark.asyncio
    async def test_fetch_catalog_uses_timeout(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test catalog fetch uses appropriate timeout."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            await karaokenerds_client.fetch_catalog()

            # Verify AsyncClient was called with timeout
            mock_client.assert_called_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_fetch_catalog_failure(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test catalog fetch failure raises error."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            with pytest.raises(ExternalServiceError) as exc_info:
                await karaokenerds_client.fetch_catalog()

            assert "KaraokeNerds" in str(exc_info.value)
            assert "503" in str(exc_info.value)


class TestParseSong:
    """Tests for parse_song method."""

    def test_parse_song_basic(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing a basic song."""
        data = {
            "id": 123,
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
        }

        song = karaokenerds_client.parse_song(data)

        assert isinstance(song, KaraokeSong)
        assert song.artist == "Queen"
        assert song.title == "Bohemian Rhapsody"
        assert song.id == "queen-bohemian-rhapsody"

    def test_parse_song_with_url(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing song with URL."""
        data = {
            "id": 456,
            "artist": "Journey",
            "title": "Don't Stop Believin'",
            "url": "https://example.com/song",
        }

        song = karaokenerds_client.parse_song(data)

        assert len(song.sources) == 1
        assert song.sources[0].source == "karaokenerds"
        assert song.sources[0].external_id == "456"
        assert song.sources[0].url == "https://example.com/song"

    def test_parse_song_with_duration(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing song with duration."""
        data = {
            "id": 789,
            "artist": "Adele",
            "title": "Rolling in the Deep",
            "duration_ms": 228000,
        }

        song = karaokenerds_client.parse_song(data)

        assert song.duration_ms == 228000

    def test_parse_song_with_genres(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing song with genres."""
        data = {
            "id": 101,
            "artist": "Michael Jackson",
            "title": "Thriller",
            "genres": ["pop", "dance", "r&b"],
        }

        song = karaokenerds_client.parse_song(data)

        assert song.genres == ["pop", "dance", "r&b"]

    def test_parse_song_strips_whitespace(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing strips whitespace from artist and title."""
        data = {
            "id": 102,
            "artist": "  Queen  ",
            "title": "  Bohemian Rhapsody  ",
        }

        song = karaokenerds_client.parse_song(data)

        assert song.artist == "Queen"
        assert song.title == "Bohemian Rhapsody"

    def test_parse_song_handles_missing_optional_fields(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test parsing handles missing optional fields."""
        data = {
            "artist": "Test Artist",
            "title": "Test Song",
        }

        song = karaokenerds_client.parse_song(data)

        assert song.artist == "Test Artist"
        assert song.title == "Test Song"
        assert song.duration_ms is None
        assert song.genres == []

    def test_parse_song_generates_normalized_id(
        self, karaokenerds_client: KaraokeNerdsClient
    ) -> None:
        """Test song ID is normalized slug."""
        data = {
            "id": 1,
            "artist": "The Beatles",
            "title": "Here Comes The Sun (Remastered)",
        }

        song = karaokenerds_client.parse_song(data)

        # ID should be lowercased, hyphenated
        assert song.id == "the-beatles-here-comes-the-sun-remastered"
