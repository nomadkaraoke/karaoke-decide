"""Genre ids and the Spotify/MusicBrainz genre-string to quiz-genre-id map."""

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


def map_genres(raw: Iterable[str]) -> list[str]:
    """Map raw genre strings to quiz genre ids.

    Every matching keyword contributes its id (``"classic rock"`` gives
    ``["classic-rock", "rock"]``). Result is de-duplicated, ordered by first
    match (longest keyword first within each input string).
    """
    result: list[str] = []
    for genre in raw:
        text = genre.lower()
        for keyword, genre_id in _KEYWORDS_LONGEST_FIRST:
            if keyword in text and genre_id not in result:
                result.append(genre_id)
    return result
