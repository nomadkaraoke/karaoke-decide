"""In-memory catalog lookup for instant track matching.

Loads the entire karaoke catalog into a dictionary on startup,
enabling O(1) lookups instead of BigQuery queries during sync.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

logger = logging.getLogger(__name__)


# Patterns to remove from track titles (must match TrackMatcher)
TITLE_REMOVE_PATTERNS = [
    r"\s*\(feat\.?\s+[^)]+\)",  # (feat. Artist)
    r"\s*\(ft\.?\s+[^)]+\)",  # (ft. Artist)
    r"\s*\(featuring\s+[^)]+\)",  # (featuring Artist)
    r"\s*\(with\s+[^)]+\)",  # (with Artist)
    r"\s*\[feat\.?\s+[^]]+\]",  # [feat. Artist]
    r"\s*\[ft\.?\s+[^]]+\]",  # [ft. Artist]
    r"\s*-\s*remaster(ed)?\s*\d*",  # - Remastered 2011
    r"\s*\(remaster(ed)?\s*\d*\)",  # (Remastered 2011)
    r"\s*\[remaster(ed)?\s*\d*\]",  # [Remastered 2011]
    r"\s*\(live[^)]*\)",  # (Live)
    r"\s*\[live[^]]*\]",  # [Live]
    r"\s*\(radio\s*(edit|mix)\)",  # (Radio Edit)
    r"\s*\(single\s*version\)",  # (Single Version)
    r"\s*\(album\s*version\)",  # (Album Version)
    r"\s*\(original\s*mix\)",  # (Original Mix)
    r"\s*\(explicit\)",  # (Explicit)
    r"\s*\(clean\)",  # (Clean)
]

# Patterns to remove from artist names (must match TrackMatcher)
ARTIST_REMOVE_PATTERNS = [
    r"\s*feat\.?\s+.*$",  # feat. Another Artist
    r"\s*ft\.?\s+.*$",  # ft. Another Artist
    r"\s*featuring\s+.*$",  # featuring Another Artist
    r"\s*\bwith\b\s+.*$",  # with Another Artist
]

# Compiled patterns
_compiled_title_patterns = [re.compile(p, re.IGNORECASE) for p in TITLE_REMOVE_PATTERNS]
_compiled_artist_patterns = [re.compile(p, re.IGNORECASE) for p in ARTIST_REMOVE_PATTERNS]


def _normalize_text(text: str) -> str:
    """Normalize text for matching (same as TrackMatcher)."""
    if not text:
        return ""
    result = text.lower()
    result = re.sub(r"[^a-z0-9 ]", " ", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def _normalize_title(title: str) -> str:
    """Normalize a track title for matching."""
    if not title:
        return ""
    result = title
    for pattern in _compiled_title_patterns:
        result = pattern.sub("", result)
    return _normalize_text(result)


def _normalize_artist(artist: str) -> str:
    """Normalize an artist name for matching."""
    if not artist:
        return ""
    result = artist
    for pattern in _compiled_artist_patterns:
        result = pattern.sub("", result)
    return _normalize_text(result)


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
        norm_artist = _normalize_artist(artist)
        norm_title = _normalize_title(title)
        return f"{norm_artist}:{norm_title}"


# Global singleton instance
_catalog_lookup: CatalogLookup | None = None


def get_catalog_lookup() -> CatalogLookup:
    """Get the global catalog lookup instance."""
    global _catalog_lookup
    if _catalog_lookup is None:
        _catalog_lookup = CatalogLookup()
    return _catalog_lookup
