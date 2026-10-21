"""In-memory catalog lookup for instant track matching.

Loads the entire karaoke catalog into a dictionary on startup,
enabling O(1) lookups instead of BigQuery queries during sync.
"""

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from karaoke_decide.catalog.keys import match_key, normalize_artist, normalize_text, normalize_title

if TYPE_CHECKING:
    from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

logger = logging.getLogger(__name__)


# Normalization lives in karaoke_decide.catalog.keys (shared with the catalog.db builder).
_normalize_text = normalize_text
_normalize_title = normalize_title
_normalize_artist = normalize_artist


@dataclass
class CatalogEntry:
    """Lightweight catalog entry for in-memory storage."""

    id: int
    artist: str
    title: str
    brands: str
    brand_count: int


class CatalogLookup:
    """In-memory catalog for instant track matching.

    Loads the entire karaoke catalog (~275K songs) into a dictionary,
    enabling O(1) lookups during sync instead of BigQuery queries.

    Memory footprint: ~55 MB for 275K entries.
    """

    def __init__(self) -> None:
        """Initialize empty catalog lookup."""
        self._lookup: dict[str, CatalogEntry] = {}
        self._loaded = False
        self._entry_count = 0

    @property
    def is_loaded(self) -> bool:
        """Check if catalog is loaded."""
        return self._loaded

    @property
    def entry_count(self) -> int:
        """Number of entries in catalog."""
        return self._entry_count

    def load_from_bigquery(self, bigquery_service: "BigQueryCatalogService") -> None:
        """Load entire catalog from BigQuery into memory.

        Args:
            bigquery_service: BigQuery catalog service.
        """
        if self._loaded:
            logger.info("Catalog already loaded, skipping")
            return

        start_time = time.time()
        logger.info("Loading karaoke catalog into memory...")

        # Get all songs from BigQuery
        all_songs = bigquery_service.get_all_songs()

        # Build lookup dictionary
        for song in all_songs:
            key = self._make_key(song.artist, song.title)
            self._lookup[key] = CatalogEntry(
                id=song.id,
                artist=song.artist,
                title=song.title,
                brands=song.brands,
                brand_count=song.brand_count,
            )

        self._entry_count = len(self._lookup)
        self._loaded = True

        elapsed = time.time() - start_time
        logger.info(f"Loaded {self._entry_count:,} songs into catalog lookup in {elapsed:.2f}s")

    def match(self, artist: str, title: str) -> CatalogEntry | None:
        """Look up a track in the catalog.

        Args:
            artist: Track artist name.
            title: Track title.

        Returns:
            CatalogEntry if found, None otherwise.
        """
        if not self._loaded:
            logger.warning("Catalog not loaded, returning None")
            return None

        key = self._make_key(artist, title)
        return self._lookup.get(key)

    def _make_key(self, artist: str, title: str) -> str:
        """Create normalized lookup key from artist and title."""
        return match_key(artist, title)


# Global singleton instance
_catalog_lookup: CatalogLookup | None = None


def get_catalog_lookup() -> CatalogLookup:
    """Get the global catalog lookup instance."""
    global _catalog_lookup
    if _catalog_lookup is None:
        _catalog_lookup = CatalogLookup()
    return _catalog_lookup
