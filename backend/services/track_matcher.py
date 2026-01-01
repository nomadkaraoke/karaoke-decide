"""Track matching service for normalizing and matching listening history to catalog."""

import logging
import re
from dataclasses import dataclass
from typing import Any

from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService, SongResult

logger = logging.getLogger(__name__)


@dataclass
class MatchedTrack:
    """A track from listening history matched to a catalog song."""

    original_artist: str
    original_title: str
    normalized_artist: str
    normalized_title: str
    catalog_song: SongResult | None  # None if no match found
    match_confidence: float  # 0.0 to 1.0

    # Source track metadata (optional, for storing unmatched tracks)
    spotify_popularity: int | None = None
    duration_ms: int | None = None
    explicit: bool = False


class TrackMatcher:
    """Service for matching listening history tracks to karaoke catalog.

    Uses text normalization to improve matching accuracy between
    streaming service track names and karaoke catalog entries.
    """

    # Patterns to remove from track titles
    TITLE_REMOVE_PATTERNS = [
        r"\s*\(feat\.?\s+[^)]+\)",  # (feat. Artist) or (feat Artist)
        r"\s*\(ft\.?\s+[^)]+\)",  # (ft. Artist) or (ft Artist)
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

    # Patterns to remove from artist names
    # Note: Intentionally NOT including "&" or "," patterns as they would
    # incorrectly truncate legitimate names like "Simon & Garfunkel" or
    # "Crosby, Stills, Nash & Young"
    ARTIST_REMOVE_PATTERNS = [
        r"\s*feat\.?\s+.*$",  # feat. Another Artist
        r"\s*ft\.?\s+.*$",  # ft. Another Artist
        r"\s*featuring\s+.*$",  # featuring Another Artist
        r"\s*\bwith\b\s+.*$",  # with Another Artist (word boundary to avoid "Saoirse")
    ]

    def __init__(self, catalog_service: BigQueryCatalogService):
        """Initialize the track matcher.

        Args:
            catalog_service: BigQuery catalog service for searching songs.
        """
        self.catalog_service = catalog_service
        self._compiled_title_patterns = [re.compile(p, re.IGNORECASE) for p in self.TITLE_REMOVE_PATTERNS]
        self._compiled_artist_patterns = [re.compile(p, re.IGNORECASE) for p in self.ARTIST_REMOVE_PATTERNS]

    def normalize_text(self, text: str) -> str:
        """Normalize text for matching.

        Performs:
        - Lowercase conversion
        - ALL punctuation removal (including apostrophes)
        - Whitespace normalization
        - Strip leading/trailing whitespace

        NOTE: Removes apostrophes to match BigQuery REGEXP_REPLACE which can't
        easily include apostrophes in the character class.

        Args:
            text: Text to normalize.

        Returns:
            Normalized text string.
        """
        if not text:
            return ""

        # Lowercase
        result = text.lower()

        # Remove ALL punctuation (including apostrophes)
        # Must match BigQuery: r'[^a-z0-9 ]'
        result = re.sub(r"[^a-z0-9 ]", " ", result)

        # Collapse multiple whitespace to single space
        result = re.sub(r"\s+", " ", result)

        # Strip leading/trailing whitespace
        result = result.strip()

        return result

    def normalize_title(self, title: str) -> str:
        """Normalize a track title for matching.

        Removes common suffixes like (feat. Artist), (Remastered), etc.
        then applies general text normalization.

        Args:
            title: Track title to normalize.

        Returns:
            Normalized title string.
        """
        if not title:
            return ""

        result = title

        # Remove patterns like (feat. X), (Remastered), etc.
        for pattern in self._compiled_title_patterns:
            result = pattern.sub("", result)

        return self.normalize_text(result)

    def normalize_artist(self, artist: str) -> str:
        """Normalize an artist name for matching.

        Removes featured artists and applies general text normalization.
        Note: This keeps only the primary artist.

        Args:
            artist: Artist name to normalize.

        Returns:
            Normalized artist name string.
        """
        if not artist:
            return ""

        result = artist

        # Remove featured artists
        for pattern in self._compiled_artist_patterns:
            result = pattern.sub("", result)

        return self.normalize_text(result)

    async def match_single_track(self, artist: str, title: str) -> MatchedTrack:
        """Match a single track to the karaoke catalog.

        Uses exact normalized matching against the BigQuery catalog.

        Args:
            artist: Track artist name.
            title: Track title.

        Returns:
            MatchedTrack with catalog_song populated if match found.
        """
        normalized_artist = self.normalize_artist(artist)
        normalized_title = self.normalize_title(title)

        # Search the catalog using the normalized values
        # Using a combined query to find exact matches
        query = f"{normalized_artist} {normalized_title}"
        results = self.catalog_service.search_songs(query, limit=10)

        catalog_song = None
        confidence = 0.0

        for result in results:
            result_artist = self.normalize_artist(result.artist)
            result_title = self.normalize_title(result.title)

            # Check for exact match
            if result_artist == normalized_artist and result_title == normalized_title:
                catalog_song = result
                confidence = 1.0
                break

            # Check for partial matches (title matches, artist similar)
            if result_title == normalized_title:
                # Artist might be slightly different
                if normalized_artist in result_artist or result_artist in normalized_artist:
                    catalog_song = result
                    confidence = 0.9
                    break

        return MatchedTrack(
            original_artist=artist,
            original_title=title,
            normalized_artist=normalized_artist,
            normalized_title=normalized_title,
            catalog_song=catalog_song,
            match_confidence=confidence,
        )

    async def batch_match_tracks(
        self,
        tracks: list[dict[str, Any]],
    ) -> list[MatchedTrack]:
        """Match a batch of tracks to the karaoke catalog.

        Uses a single BigQuery query per batch for efficiency, instead of
        one query per track. This dramatically improves performance for
        large listening histories (900+ tracks).

        Args:
            tracks: List of dicts with 'artist', 'title', and optionally
                   'popularity', 'duration_ms', 'explicit' keys.

        Returns:
            List of MatchedTrack results in same order as input.
        """
        if not tracks:
            return []

        logger.info(f"Track matcher: received {len(tracks)} tracks to match")

        # First, normalize all tracks and build lookup structures
        # (orig_artist, orig_title, norm_artist, norm_title, popularity, duration_ms, explicit)
        normalized_tracks: list[tuple[str, str, str, str, int | None, int | None, bool]] = []
        for track in tracks:
            artist = track.get("artist", "")
            title = track.get("title", "")
            popularity = track.get("popularity")
            duration_ms = track.get("duration_ms")
            explicit = track.get("explicit", False)
            normalized_tracks.append(
                (
                    artist,
                    title,
                    self.normalize_artist(artist),
                    self.normalize_title(title),
                    popularity,
                    duration_ms,
                    explicit,
                )
            )

        # Build list of unique (normalized_artist, normalized_title) tuples for batch query
        unique_normalized = list({(nt[2], nt[3]) for nt in normalized_tracks if nt[2] and nt[3]})
        logger.info(f"Track matcher: {len(unique_normalized)} unique normalized tracks to query")

        # Log a sample of normalized tracks for debugging
        if unique_normalized:
            sample = unique_normalized[:5]
            for artist, title in sample:
                logger.info(f"Track matcher sample: '{artist}' - '{title}'")

        # Execute batch query to get all matches at once
        matched_songs = self.catalog_service.batch_match_tracks(unique_normalized)
        logger.info(f"Track matcher: BigQuery returned {len(matched_songs)} matches")

        # Build results maintaining original order
        results: list[MatchedTrack] = []
        for orig_artist, orig_title, norm_artist, norm_title, popularity, duration_ms, explicit in normalized_tracks:
            # Look up match using normalized values
            key = (norm_artist, norm_title)
            catalog_song = matched_songs.get(key)

            results.append(
                MatchedTrack(
                    original_artist=orig_artist,
                    original_title=orig_title,
                    normalized_artist=norm_artist,
                    normalized_title=norm_title,
                    catalog_song=catalog_song,
                    match_confidence=1.0 if catalog_song else 0.0,
                    spotify_popularity=popularity,
                    duration_ms=duration_ms,
                    explicit=explicit,
                )
            )

        return results

    def get_match_stats(self, matches: list[MatchedTrack]) -> dict[str, int | float]:
        """Get statistics about a batch of matches.

        Args:
            matches: List of MatchedTrack results.

        Returns:
            Dict with 'total', 'matched', 'unmatched' counts.
        """
        total = len(matches)
        matched = sum(1 for m in matches if m.catalog_song is not None)
        unmatched = total - matched

        return {
            "total": total,
            "matched": matched,
            "unmatched": unmatched,
            "match_rate": matched / total if total > 0 else 0.0,
        }


# Lazy initialization
_track_matcher: TrackMatcher | None = None


def get_track_matcher(
    catalog_service: BigQueryCatalogService | None = None,
) -> TrackMatcher:
    """Get the track matcher instance.

    Args:
        catalog_service: Optional catalog service override (for testing).

    Returns:
        TrackMatcher instance.
    """
    global _track_matcher

    if _track_matcher is None or catalog_service is not None:
        if catalog_service is None:
            catalog_service = BigQueryCatalogService()
        _track_matcher = TrackMatcher(catalog_service)

    return _track_matcher
