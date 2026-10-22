"""Signal weights for recommendations v2 (decide ``docs/VISION.md`` section Recommendation Engine Weighting).

One module so scoring quality iteration touches a single file.
"""

from __future__ import annotations

# --- artist signals ---------------------------------------------------------
ARTIST_TOP_FLOOR = 0.5
ARTIST_TOP_RANK_SPAN = 200
QUIZ_LOVE = 0.9
QUIZ_LIKE = 0.7
QUIZ_OCCASIONALLY = 0.45
ARTIST_MANUAL = 0.8
ARTIST_FROM_SONGS_BASE = 0.4
ARTIST_FROM_SONGS_PER_SONG = 0.1
ARTIST_FROM_SONGS_CAP = 0.9

QUIZ_AFFINITY = {"love": QUIZ_LOVE, "like": QUIZ_LIKE, "occasionally": QUIZ_OCCASIONALLY}

# --- song signals -----------------------------------------------------------
SONG_LASTFM_HIGH = 1.0  # 10+ plays
SONG_LASTFM_MID = 0.7  # 3-9 plays
SONG_LASTFM_LOW = 0.4  # 1-2 plays
SONG_SPOTIFY_SAVED = 0.9
SONG_SPOTIFY_TOP = 0.8
SONG_MANUAL = 1.0
SONG_YTMUSIC = 0.8
# Explicit user adds: known_songs / enjoy_singing (known_songs_service) and "manual" (love endpoint).
MANUAL_SOURCES = frozenset({"manual", "known_songs", "enjoy_singing"})
SONG_DEFAULT = 0.6  # quiz or any other source


def artist_top(rank: int | None) -> float:
    """Spotify / Last.fm / ListenBrainz top artist: ``1.0 * max(0.5, 1 - rank/200)``.

    A missing rank gets the floor (we know they listen, not how much)."""
    if rank is None:
        return ARTIST_TOP_FLOOR
    return max(ARTIST_TOP_FLOOR, 1.0 - max(rank, 0) / ARTIST_TOP_RANK_SPAN)


def artist_from_songs(n_songs: int) -> float:
    """``min(0.9, 0.4 + 0.1 * n_songs)``."""
    return min(ARTIST_FROM_SONGS_CAP, ARTIST_FROM_SONGS_BASE + ARTIST_FROM_SONGS_PER_SONG * n_songs)


def lastfm_song(playcount: int | None) -> float:
    if playcount is not None and playcount >= 10:
        return SONG_LASTFM_HIGH
    if playcount is not None and playcount >= 3:
        return SONG_LASTFM_MID
    return SONG_LASTFM_LOW


def song_weight(doc: dict[str, object]) -> float:
    """Weight of one ``user_songs`` doc by its ``source``."""
    source = doc.get("source")
    loved = doc.get("loved")  # "Love it" marker (song_feedback_service.is_loved); None on legacy rows
    if loved is True or (source in MANUAL_SOURCES and not (source == "manual" and loved is False)):
        return SONG_MANUAL
    if source == "lastfm":
        return lastfm_song(to_int(doc.get("playcount")))
    if source == "spotify":
        return SONG_SPOTIFY_TOP if doc.get("rank") is not None else SONG_SPOTIFY_SAVED
    if source in ("youtube_music", "ytmusic"):
        return SONG_YTMUSIC
    return SONG_DEFAULT


def to_int(v: object) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float | str):
        return None
    try:
        return int(v)
    except ValueError:
        return None


# --- scoring (scoring.score) ------------------------------------------------
# score = sum(weight * feature); every feature is in [0, 1].
SCORE_AFFINITY = 0.30  # artist affinity (the artist's context weight)
SCORE_SIMILAR = 0.15  # max over seeds of weight(seed) * similarity(seed, artist)
SCORE_KNOWN = 0.15  # known-song weight (a boost and a label, never an exclusion)
SCORE_GENRE = 0.12  # song shares a quiz genre (ignoring "other")
SCORE_DECADE = 0.08  # song's decade is a quiz decade
SCORE_ENERGY = 0.08  # closeness to the quiz energy preference
SCORE_POPULARITY = 0.07  # popularity / 100
SCORE_BRANDS = 0.05  # min(brand_count, BRAND_CAP) / BRAND_CAP
BRAND_CAP = 20
ENERGY_MEDIUM_CENTER = 0.55  # "medium" peaks here and reaches 0 at +-0.5

# quiz crowd-pleaser preference
HITS_POPULARITY_BONUS = 0.05  # "hits": + bonus * pop
DEEP_CUTS_OBSCURITY_BONUS = 0.03  # "deep_cuts": + bonus * (1 - pop) ...
DEEP_CUTS_POPULARITY_PENALTY = 0.05  # ... - penalty * pop
