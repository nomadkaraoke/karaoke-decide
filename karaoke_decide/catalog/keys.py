"""Catalog join keys shared by the app backend and the catalog.db builder."""

import re

from karaoke_decide.song_search.text_normalize import identity_normalize, song_key

__all__ = [
    "artist_key",
    "loose_keys",
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


_BRACKETED_RE = re.compile(r"\s*[\[(][^\])]*[\])]")
# Separators that can join the names of a multi-artist credit ("A & B", "A, B", "A x B", "A with B").
# "feat./ft./featuring" is not here: identity_normalize already drops that clause. A bare "/" is
# not a separator ("AC/DC").
_CREDIT_SPLIT_RE = re.compile(r"\s+(?:&|and|x|\+|with|vs\.?)\s+|\s*,\s*|\s*;\s*", re.IGNORECASE)


def _squash(text: str) -> str:
    """identity_normalize, leading "the" dropped, spaces removed: "U.S.A." == "Usa", "I M" == "I'm"."""
    norm = identity_normalize(text)
    if norm.startswith("the ") and len(norm) > 4:
        norm = norm[4:]
    return norm.replace(" ", "")


def _primary_artist(artist: str, known_artists: frozenset[str]) -> str | None:
    """The lead name of a real multi-artist credit, else None.

    Only when every part is a known artist AND the whole credit is not itself one, so "Simon and
    Garfunkel", "Earth, Wind & Fire" or "Hall & Oates" are never reduced to their first member."""
    if artist_key(artist) in known_artists:
        return None
    parts = [p for p in _CREDIT_SPLIT_RE.split(artist.strip()) if p.strip()]
    if len(parts) < 2 or not all(artist_key(p) in known_artists for p in parts):
        return None
    return parts[0]


def loose_keys(artist: str | None, title: str | None, known_artists: frozenset[str] = frozenset()) -> frozenset[str]:
    """Aggressive "same song?" keys: punctuation/space-insensitive, bracket-free title.

    Two (artist, title) pairs are the same song for karaoke-availability purposes when their key
    sets intersect. Looser than ``song_key``/``match_key`` (those split "Party In The U.S.A." from
    "Party In The Usa"). With ``known_artists`` (``artist_key`` values) a genuine multi-artist
    credit also answers to its lead artist: "David Guetta & Bebe Rexha" -> "David Guetta".
    Empty when either side is blank."""
    artist, title = artist or "", title or ""
    artists = {_squash(artist)}
    lead = _primary_artist(artist, known_artists) if known_artists else None
    if lead:
        artists.add(_squash(lead))
    titles = {_squash(title), _squash(_BRACKETED_RE.sub("", title))}
    return frozenset(f"{a}:{t}" for a in artists for t in titles if a and t)
