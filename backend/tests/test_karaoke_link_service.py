"""Unit tests for KaraokeLinkService."""

from unittest.mock import MagicMock

import pytest

from backend.services.karaoke_link_service import (
    KaraokeLink,
    KaraokeLinkService,
    KaraokeLinkType,
)


class TestKaraokeLinkService:
    """Tests for KaraokeLinkService."""

    @pytest.fixture
    def mock_settings(self) -> MagicMock:
        """Create mock settings."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_settings: MagicMock) -> KaraokeLinkService:
        """Create service with mock settings."""
        return KaraokeLinkService(mock_settings)

    def test_get_youtube_search_url(self, service: KaraokeLinkService) -> None:
        """Test YouTube search URL generation."""
        url = service.get_youtube_search_url("Queen", "Bohemian Rhapsody")

        assert "youtube.com/results" in url
        assert "search_query=" in url
        assert "Queen" in url
        assert "Bohemian" in url
        assert "karaoke" in url

    def test_get_youtube_search_url_encodes_special_chars(self, service: KaraokeLinkService) -> None:
        """Test YouTube URL properly encodes special characters."""
        url = service.get_youtube_search_url("AC/DC", "Back in Black")

        assert "youtube.com/results" in url
        # URL should be encoded (/ becomes %2F)
        assert "AC%2FDC" in url or "AC/DC" not in url

    def test_get_generator_url(self, service: KaraokeLinkService) -> None:
        """Test Karaoke Generator URL generation."""
        url = service.get_generator_url("Queen", "Bohemian Rhapsody")

        assert "gen.nomadkaraoke.com" in url
        assert "artist=" in url
        assert "title=" in url
        assert "Queen" in url
        assert "Bohemian" in url

    def test_get_generator_url_encodes_special_chars(self, service: KaraokeLinkService) -> None:
        """Test Generator URL properly encodes special characters."""
        url = service.get_generator_url("Guns N' Roses", "Sweet Child O' Mine")

        assert "gen.nomadkaraoke.com" in url
        # Apostrophe should be encoded
        assert "'" not in url or "%27" in url

    def test_get_links_returns_all_link_types(self, service: KaraokeLinkService) -> None:
        """Test get_links returns all available link types."""
        links = service.get_links("Queen", "Bohemian Rhapsody")

        assert len(links) == 2
        types = {link.type for link in links}
        assert KaraokeLinkType.YOUTUBE_SEARCH in types
        assert KaraokeLinkType.KARAOKE_GENERATOR in types

    def test_get_links_structure(self, service: KaraokeLinkService) -> None:
        """Test get_links returns properly structured KaraokeLink objects."""
        links = service.get_links("Queen", "Bohemian Rhapsody")

        for link in links:
            assert isinstance(link, KaraokeLink)
            assert link.url is not None
            assert link.label is not None
            assert link.description is not None
            assert link.type in KaraokeLinkType

    def test_youtube_link_has_correct_metadata(self, service: KaraokeLinkService) -> None:
        """Test YouTube link has correct label and description."""
        links = service.get_links("Queen", "Bohemian Rhapsody")
        youtube_link = next(link for link in links if link.type == KaraokeLinkType.YOUTUBE_SEARCH)

        assert "YouTube" in youtube_link.label
        assert "youtube" in youtube_link.description.lower() or "video" in youtube_link.description.lower()

    def test_generator_link_has_correct_metadata(self, service: KaraokeLinkService) -> None:
        """Test Generator link has correct label and description."""
        links = service.get_links("Queen", "Bohemian Rhapsody")
        gen_link = next(link for link in links if link.type == KaraokeLinkType.KARAOKE_GENERATOR)

        assert "Generator" in gen_link.label or "Create" in gen_link.label
        assert "generate" in gen_link.description.lower() or "custom" in gen_link.description.lower()

    def test_handles_empty_artist(self, service: KaraokeLinkService) -> None:
        """Test service handles empty artist gracefully."""
        links = service.get_links("", "Bohemian Rhapsody")

        assert len(links) == 2
        for link in links:
            assert "Bohemian" in link.url

    def test_handles_empty_title(self, service: KaraokeLinkService) -> None:
        """Test service handles empty title gracefully."""
        links = service.get_links("Queen", "")

        assert len(links) == 2
        for link in links:
            assert "Queen" in link.url

    def test_handles_unicode_characters(self, service: KaraokeLinkService) -> None:
        """Test service handles unicode characters."""
        links = service.get_links("Björk", "Jóga")

        assert len(links) == 2
        # URLs should be properly encoded
        for link in links:
            assert link.url is not None


class TestKaraokeLinkType:
    """Tests for KaraokeLinkType enum."""

    def test_enum_values(self) -> None:
        """Test enum has expected values."""
        assert KaraokeLinkType.YOUTUBE_SEARCH.value == "youtube_search"
        assert KaraokeLinkType.KARAOKE_GENERATOR.value == "karaoke_generator"

    def test_enum_is_string(self) -> None:
        """Test enum values are strings for JSON serialization."""
        for link_type in KaraokeLinkType:
            assert isinstance(link_type.value, str)
