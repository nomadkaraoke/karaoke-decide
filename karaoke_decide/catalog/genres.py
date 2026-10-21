"""Genre ids and the Spotify/MusicBrainz genre-string to quiz-genre-id map."""

import re
from collections.abc import Iterable

# The 21 quiz genres plus "other" ("Something else"); matches decide web quiz/page.tsx.
GENRE_IDS: tuple[str, ...] = (
    "pop",
    "rock",
    "hiphop",
    "rnb",
    "country",
    "electronic",
    "metal",
    "punk",
    "emo",
    "grunge",
    "jazz",
    "latin",
    "indie",
    "kpop",
    "disco",
    "classic-rock",
    "folk",
    "blues",
    "ska",
    "musical",
    "reggae",
    "other",
)

# Substring keyword -> genre id (carried over from QuizService._map_spotify_genres_to_ids).
_GENRE_KEYWORDS: dict[str, str] = {
    "pop": "pop",
    "rock": "rock",
    "hip hop": "hiphop",
    "r&b": "rnb",
    "country": "country",
    "electro": "electronic",
    "dance": "electronic",
    "metal": "metal",
    "jazz": "jazz",
    "latin": "latin",
    "reggaeton": "latin",
    "indie": "indie",
    "alternative": "indie",
    "k-pop": "kpop",
    "disco": "disco",
    "funk": "disco",
    "soul": "rnb",
    "classic rock": "classic-rock",
    "broadway": "musical",
    "reggae": "reggae",
    "punk": "punk",
    "emo": "emo",
    "grunge": "grunge",
    "folk": "folk",
    "blues": "blues",
    "ska": "ska",
}

# Longest keyword first so specific genres ("classic rock") come before generic ones ("rock").
_KEYWORDS_LONGEST_FIRST: tuple[tuple[str, str], ...] = tuple(
    sorted(_GENRE_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True)
)


def _is_compound(keyword: str) -> bool:
    return " " in keyword or "-" in keyword


def map_genres(raw: Iterable[str]) -> list[str]:
    """Map raw genre strings to quiz genre ids.

    Every matching keyword contributes its id (``"classic rock"`` gives
    ``["classic-rock", "rock"]``, ``"k-pop"`` gives ``["kpop", "pop"]``).
    Compound keywords (containing a space or hyphen) decompose into their
    parts, but a single-word keyword is atomic: a shorter keyword found only
    inside it is ignored (``"reggaeton"`` gives ``["latin"]``, not reggae).
    Result is de-duplicated, ordered by first match (longest keyword first).
    """
    result: list[str] = []
    for genre in raw:
        text = genre.lower()
        atomic_spans = [
            m.span()
            for keyword, _ in _KEYWORDS_LONGEST_FIRST
            if not _is_compound(keyword)
            for m in re.finditer(re.escape(keyword), text)
        ]
        for keyword, genre_id in _KEYWORDS_LONGEST_FIRST:
            for m in re.finditer(re.escape(keyword), text):
                start, end = m.span()
                swallowed = any(
                    (s, e) != (start, end) and s <= start and end <= e and e - s > end - start for s, e in atomic_spans
                )
                if not swallowed:
                    if genre_id not in result:
                        result.append(genre_id)
                    break
    return result
