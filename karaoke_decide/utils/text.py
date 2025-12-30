"""Text normalization utilities for Karaoke Decide."""

import re
import unicodedata

from slugify import slugify


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for matching.

    - Lowercase
    - Remove "The " prefix
    - Normalize unicode characters
    - Strip extra whitespace
    """
    artist = artist.strip().lower()

    # Remove "The " prefix
    if artist.startswith("the "):
        artist = artist[4:]

    # Normalize unicode
    artist = unicodedata.normalize("NFKD", artist)
    artist = artist.encode("ascii", "ignore").decode("ascii")

    # Collapse whitespace
    artist = re.sub(r"\s+", " ", artist)

    return artist.strip()


def normalize_title(title: str) -> str:
    """Normalize a song title for matching.

    - Lowercase
    - Remove parenthetical content (remix info, etc.)
    - Normalize unicode characters
    - Strip extra whitespace
    """
    title = title.strip().lower()

    # Remove parenthetical content like "(Radio Edit)", "(Remastered)"
    title = re.sub(r"\s*\([^)]*\)", "", title)
    title = re.sub(r"\s*\[[^\]]*\]", "", title)

    # Remove common suffixes
    suffixes = [
        " - remastered",
        " - radio edit",
        " - single version",
        " - album version",
        " remastered",
        " remaster",
    ]
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)]

    # Normalize unicode
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")

    # Collapse whitespace
    title = re.sub(r"\s+", " ", title)

    return title.strip()


def generate_song_id(artist: str, title: str) -> str:
    """Generate a normalized song ID from artist and title.

    Returns a URL-safe slug like "queen-bohemian-rhapsody".
    """
    normalized = f"{normalize_artist(artist)}-{normalize_title(title)}"
    return slugify(normalized, lowercase=True)
