"""Catalog join keys shared by the app backend and the catalog.db builder."""

import re

from karaoke_decide.song_search.text_normalize import identity_normalize, song_key

__all__ = [
    "artist_key",
    "match_key",
    "song_key",
    "normalize_artist",
    "normalize_text",
    "normalize_title",
]

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


def normalize_text(text: str) -> str:
    """Normalize text for matching (same as TrackMatcher)."""
    if not text:
        return ""
    result = text.lower()
    result = re.sub(r"[^a-z0-9 ]", " ", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def normalize_title(title: str) -> str:
    """Normalize a track title for matching."""
    if not title:
        return ""
    result = title
    for pattern in _compiled_title_patterns:
        result = pattern.sub("", result)
    return normalize_text(result)


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for matching."""
    if not artist:
        return ""
    result = artist
    for pattern in _compiled_artist_patterns:
        result = pattern.sub("", result)
    return normalize_text(result)


def artist_key(name: str | None) -> str:
    """Canonical artist identity key (feat. clause stripped, accents folded)."""
    return identity_normalize(name)


def match_key(artist: str, title: str) -> str:
    """Track-matching key; byte-identical to the legacy CatalogLookup._make_key."""
    return f"{normalize_artist(artist)}:{normalize_title(title)}"
