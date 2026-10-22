"""Tests for CatalogLookup service (catalog.db kn_song_map lookups)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.config import BackendSettings
from backend.services.catalog_lookup import (
    CatalogEntry,
    CatalogLookup,
    _normalize_artist,
    _normalize_text,
    _normalize_title,
    get_catalog_lookup,
)
from backend.services.catalog_store import CatalogStore
from karaoke_decide.catalog import fixtures


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

    def test_not_loaded_while_store_warming_up(self) -> None:
        lookup = CatalogLookup(CatalogStore())
        assert not lookup.is_loaded

    def test_match_returns_none_when_not_loaded(self) -> None:
        lookup = CatalogLookup(CatalogStore())
        assert lookup.match("Queen", "Bohemian Rhapsody") is None

    def test_make_key_normalizes(self) -> None:
        """Test _make_key produces consistent normalized keys."""
        lookup = CatalogLookup(CatalogStore())
        key1 = lookup._make_key("Queen", "Bohemian Rhapsody")
        key2 = lookup._make_key("QUEEN", "BOHEMIAN RHAPSODY")
        key3 = lookup._make_key("Queen feat. Someone", "Bohemian Rhapsody (Remastered)")
        assert key1 == key2
        assert key1 == key3  # Patterns should be stripped


@pytest.fixture(scope="module")
def ready_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CatalogStore]:
    d: Path = tmp_path_factory.mktemp("catalog")
    fixtures.build(d)
    store = CatalogStore()
    store.load(BackendSettings(catalog_local_dir=str(d), song_index_local_dir=str(d / "no-index")))
    assert store.status == "ready"
    yield store
    store.close()


class TestCatalogLookupWithData:
    """Matching reads kn_song_map by match_key through CatalogReader."""

    def test_match_finds_song(self, ready_store: CatalogStore) -> None:
        lookup = CatalogLookup(ready_store)
        assert lookup.is_loaded
        assert lookup.match("Toto", "Africa") == CatalogEntry(
            id=1, artist="Toto", title="Africa", brands="CC,KV", brand_count=2
        )

    def test_match_is_case_insensitive(self, ready_store: CatalogStore) -> None:
        result = CatalogLookup(ready_store).match("TOTO", "AFRICA")
        assert result is not None and result.id == 1

    def test_match_strips_patterns(self, ready_store: CatalogStore) -> None:
        result = CatalogLookup(ready_store).match("Toto feat. Someone", "Africa (Remastered 2011)")
        assert result is not None and result.id == 1

    def test_match_returns_none_for_unknown(self, ready_store: CatalogStore) -> None:
        assert CatalogLookup(ready_store).match("Unknown Artist", "Unknown Song") is None


class TestGetCatalogLookup:
    """Tests for get_catalog_lookup singleton function."""

    def test_returns_same_instance(self) -> None:
        """Test get_catalog_lookup returns the same instance."""
        lookup1 = get_catalog_lookup()
        lookup2 = get_catalog_lookup()
        assert lookup1 is lookup2
