"""Track matching service for normalizing and matching listening history to catalog."""

import re
from dataclasses import dataclass

from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService, SongResult


@dataclass
class MatchedTrack:
    """A track from listening history matched to a catalog song."""

    original_artist: str
    original_title: str
    normalized_artist: str
    normalized_title: str
    catalog_song: SongResult | None  # None if no match found
    match_confidence: float  # 0.0 to 1.0


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
        - Punctuation removal (except apostrophes in contractions)
        - Whitespace normalization
        - Strip leading/trailing whitespace

        Args:
            text: Text to normalize.

        Returns:
            Normalized text string.
        """
        if not text:
            return ""

        # Lowercase
        result = text.lower()

        # Replace common unicode characters
        result = result.replace("'", "'").replace("'", "'")
        result = result.replace(""", '"').replace(""", '"')

        # Remove punctuation except apostrophes (for contractions like "don't")
        # Keep alphanumeric, spaces, and apostrophes
        result = re.sub(r"[^\w\s']", " ", result)

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
        tracks: list[dict[str, str]],
        batch_size: int = 50,
    ) -> list[MatchedTrack]:
        """Match a batch of tracks to the karaoke catalog.

        Processes tracks in batches for efficiency.

        Args:
            tracks: List of dicts with 'artist' and 'title' keys.
            batch_size: Number of tracks to process per batch.

        Returns:
            List of MatchedTrack results in same order as input.
        """
        results: list[MatchedTrack] = []

        for i in range(0, len(tracks), batch_size):
            batch = tracks[i : i + batch_size]

            for track in batch:
                artist = track.get("artist", "")
                title = track.get("title", "")

                matched = await self.match_single_track(artist, title)
                results.append(matched)

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
