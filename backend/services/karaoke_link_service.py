"""Service for generating karaoke links.

Provides links to various karaoke sources including YouTube search
and the Nomad Karaoke Generator.
"""

from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus

from backend.config import BackendSettings


class KaraokeLinkType(str, Enum):
    """Types of karaoke links available."""

    YOUTUBE_SEARCH = "youtube_search"
    KARAOKE_GENERATOR = "karaoke_generator"


@dataclass
class KaraokeLink:
    """A karaoke link for a song."""

    type: KaraokeLinkType
    url: str
    label: str
    description: str


class KaraokeLinkService:
    """Service for generating karaoke links.

    Generates links to various karaoke sources:
    - YouTube search (optimized for karaoke videos)
    - Nomad Karaoke Generator (for creating custom videos)
    """

    # Base URLs
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"
    KARAOKE_GENERATOR_URL = "https://gen.nomadkaraoke.com"

    def __init__(self, settings: BackendSettings):
        """Initialize the karaoke link service.

        Args:
            settings: Backend settings.
        """
        self.settings = settings

    def get_links(self, artist: str, title: str) -> list[KaraokeLink]:
        """Get all available karaoke links for a song.

        Args:
            artist: Song artist name.
            title: Song title.

        Returns:
            List of KaraokeLink objects.
        """
        return [
            self._get_youtube_search_link(artist, title),
            self._get_generator_link(artist, title),
        ]

    def get_youtube_search_url(self, artist: str, title: str) -> str:
        """Generate YouTube karaoke search URL.

        Uses an optimized search query for finding karaoke videos.

        Args:
            artist: Song artist name.
            title: Song title.

        Returns:
            YouTube search URL string.
        """
        # Optimized search query for karaoke
        query = f"{artist} {title} karaoke"
        return f"{self.YOUTUBE_SEARCH_URL}?search_query={quote_plus(query)}"

    def get_generator_url(self, artist: str, title: str) -> str:
        """Generate Nomad Karaoke Generator URL.

        Creates a link to the generator with artist/title prefilled.

        Args:
            artist: Song artist name.
            title: Song title.

        Returns:
            Karaoke Generator URL string.
        """
        # Generator accepts artist and title as query params
        return f"{self.KARAOKE_GENERATOR_URL}?artist={quote_plus(artist)}&title={quote_plus(title)}"

    def _get_youtube_search_link(self, artist: str, title: str) -> KaraokeLink:
        """Create YouTube search link object.

        Args:
            artist: Song artist name.
            title: Song title.

        Returns:
            KaraokeLink for YouTube search.
        """
        return KaraokeLink(
            type=KaraokeLinkType.YOUTUBE_SEARCH,
            url=self.get_youtube_search_url(artist, title),
            label="Search YouTube",
            description="Find existing karaoke videos on YouTube",
        )

    def _get_generator_link(self, artist: str, title: str) -> KaraokeLink:
        """Create Karaoke Generator link object.

        Args:
            artist: Song artist name.
            title: Song title.

        Returns:
            KaraokeLink for Karaoke Generator.
        """
        return KaraokeLink(
            type=KaraokeLinkType.KARAOKE_GENERATOR,
            url=self.get_generator_url(artist, title),
            label="Create with Generator",
            description="Generate a custom karaoke video with Nomad Karaoke",
        )


# Lazy initialization
_karaoke_link_service: KaraokeLinkService | None = None


def get_karaoke_link_service(
    settings: BackendSettings | None = None,
) -> KaraokeLinkService:
    """Get the karaoke link service instance.

    Args:
        settings: Optional settings override.

    Returns:
        KaraokeLinkService instance.
    """
    global _karaoke_link_service

    if _karaoke_link_service is None or settings is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()

        _karaoke_link_service = KaraokeLinkService(settings)

    return _karaoke_link_service
