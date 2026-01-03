"""Tests for CatalogLookup service."""

import pytest

from backend.services.catalog_lookup import (
    CatalogEntry,
    CatalogLookup,
    _normalize_artist,
    _normalize_text,
    _normalize_title,
    get_catalog_lookup,
)


class TestNormalizeFunctions:
    """Tests for normalization helper functions."""

    def test_normalize_text_basic(self) -> None:
        """Test basic text normalization."""
        assert _normalize_text("Hello World") == "hello world"
        assert _normalize_text("  spaced  out  ") == "spaced out"
        assert _normalize_text("It's") == "it s"

    def test_normalize_title_removes_patterns(self) -> None:
        """Test title normalization removes common patterns."""
        assert _normalize_title("Song (feat. Artist)") == "song"
        assert _normalize_title("Song (Remastered 2011)") == "song"
        assert _normalize_title("Song (Live)") == "song"
        assert _normalize_title("Song (Radio Edit)") == "song"

    def test_normalize_artist_removes_featured(self) -> None:
        """Test artist normalization removes featured artists."""
        assert _normalize_artist("Artist feat. Other") == "artist"
        assert _normalize_artist("Artist ft. Other") == "artist"
        assert _normalize_artist("Simon & Garfunkel") == "simon garfunkel"


class TestCatalogLookup:
    """Tests for CatalogLookup class."""

    def test_not_loaded_initially(self) -> None:
        """Test catalog is not loaded initially."""
        lookup = CatalogLookup()
        assert not lookup.is_loaded
        assert lookup.entry_count == 0

    def test_match_returns_none_when_not_loaded(self) -> None:
        """Test match returns None when catalog not loaded."""
        lookup = CatalogLookup()
        result = lookup.match("Queen", "Bohemian Rhapsody")
        assert result is None

    def test_make_key_normalizes(self) -> None:
        """Test _make_key produces consistent normalized keys."""
        lookup = CatalogLookup()
        key1 = lookup._make_key("Queen", "Bohemian Rhapsody")
        key2 = lookup._make_key("QUEEN", "BOHEMIAN RHAPSODY")
        key3 = lookup._make_key("Queen feat. Someone", "Bohemian Rhapsody (Remastered)")
        assert key1 == key2
        assert key1 == key3  # Patterns should be stripped


class TestCatalogLookupWithData:
    """Tests for CatalogLookup with pre-loaded data."""

    @pytest.fixture
    def loaded_lookup(self) -> CatalogLookup:
        """Create a CatalogLookup with test data."""
        lookup = CatalogLookup()
        # Manually set lookup data for testing
        lookup._lookup = {
            "queen:bohemian rhapsody": CatalogEntry(
                id=123,
                artist="Queen",
                title="Bohemian Rhapsody",
                brands="Sound Choice,Zoom",
                brand_count=2,
            ),
            "the beatles:hey jude": CatalogEntry(
                id=456,
                artist="The Beatles",
                title="Hey Jude",
                brands="Karaoke Version",
                brand_count=1,
            ),
        }
        lookup._loaded = True
        lookup._entry_count = 2
        return lookup

    def test_match_finds_song(self, loaded_lookup: CatalogLookup) -> None:
        """Test match finds a song in the catalog."""
        result = loaded_lookup.match("Queen", "Bohemian Rhapsody")
        assert result is not None
        assert result.id == 123
        assert result.artist == "Queen"
        assert result.title == "Bohemian Rhapsody"

    def test_match_is_case_insensitive(self, loaded_lookup: CatalogLookup) -> None:
        """Test match is case insensitive."""
        result = loaded_lookup.match("QUEEN", "BOHEMIAN RHAPSODY")
        assert result is not None
        assert result.id == 123

    def test_match_strips_patterns(self, loaded_lookup: CatalogLookup) -> None:
        """Test match strips common patterns from input."""
        result = loaded_lookup.match("Queen feat. David Bowie", "Bohemian Rhapsody (Remastered)")
        assert result is not None
        assert result.id == 123

    def test_match_returns_none_for_unknown(self, loaded_lookup: CatalogLookup) -> None:
        """Test match returns None for unknown song."""
        result = loaded_lookup.match("Unknown Artist", "Unknown Song")
        assert result is None


class TestGetCatalogLookup:
    """Tests for get_catalog_lookup singleton function."""

    def test_returns_same_instance(self) -> None:
        """Test get_catalog_lookup returns the same instance."""
        lookup1 = get_catalog_lookup()
        lookup2 = get_catalog_lookup()
        assert lookup1 is lookup2
