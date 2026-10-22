"""Catalog lookup for instant track matching during sync.

Matches listening-history tracks to KaraokeNerds songs through ``catalog.db``'s
``kn_song_map`` (indexed by ``match_key``), via the startup-loaded ``CatalogStore``.
There is no in-memory copy of ``karaokenerds_raw`` any more (it cost ~34 s of cold
start); while the store is warming up, ``TrackMatcher`` falls back to BigQuery.
"""

import logging
from dataclasses import dataclass

from backend.services.catalog_store import CatalogStore, get_catalog_store
from karaoke_decide.catalog.keys import normalize_artist, normalize_text, normalize_title

logger = logging.getLogger(__name__)


# Normalization lives in karaoke_decide.catalog.keys (shared with the catalog.db builder).
_normalize_text = normalize_text
_normalize_title = normalize_title
_normalize_artist = normalize_artist


@dataclass
class CatalogEntry:
    """A matched KaraokeNerds catalog song."""

    id: int
    artist: str
    title: str
    brands: str
    brand_count: int


class CatalogLookup:
    """Track matching against ``catalog.db`` (one indexed SQLite lookup per track)."""

    def __init__(self, store: CatalogStore | None = None) -> None:
        self._store = store if store is not None else get_catalog_store()

    @property
    def store(self) -> CatalogStore:
        return self._store

    def match(self, artist: str, title: str) -> CatalogEntry | None:
        """Look up a track in the catalog; None when unmatched or still warming up."""
        reader = self._store.reader
        if reader is None:
            return None
        hit = reader.match_kn(artist, title)
        if hit is None:
            return None
        return CatalogEntry(
            id=hit.kn_id, artist=hit.artist, title=hit.title, brands=hit.brands, brand_count=hit.brand_count
        )

    def lookup_song_keys(self, keys: list[str]) -> set[str]:
        """The subset of ``keys`` (song_keys) that catalog.db has karaoke for (blocking SQLite).

        Empty while the catalog is still warming up."""
        reader = self._store.reader
        if reader is None:
            return set()
        return {k for k in dict.fromkeys(keys) if reader.has_karaoke_key(k)}

    def lookup_artist_keys(self, keys: list[str]) -> dict[str, str]:
        """``{artist_key: display name}`` for the artist keys catalog.db knows (blocking SQLite)."""
        reader = self._store.reader
        if reader is None:
            return {}
        return {k: s.name for k, s in reader.artist_summaries(keys, top_songs=0).items()}


# Global singleton instance
_catalog_lookup: CatalogLookup | None = None


def get_catalog_lookup() -> CatalogLookup:
    """Get the global catalog lookup instance."""
    global _catalog_lookup
    if _catalog_lookup is None:
        _catalog_lookup = CatalogLookup()
    return _catalog_lookup
