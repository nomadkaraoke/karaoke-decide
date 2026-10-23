"""v2 user context: everything we know about a user, keyed for the catalog.

Artists come from every source (synced top artists, manual adds, quiz affinities and
manual quiz artists, plus the artists of known songs); the max weight across sources
wins and ``excluded_artists`` are removed. Songs map to the search service's
``song_key``: numeric ``song_id`` through catalog.db ``kn_song_map``, anything else
(Spotify / Last.fm / ... ids) by ``song_key(artist, title)``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from backend.services.firestore_service import FirestoreService
from backend.services.recs_v2 import weights as w
from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.text_normalize import song_key

logger = logging.getLogger(__name__)
# Ranked top artists: synced services, plus the imports (import_ingest_service) ranked by plays.
_TOP_SOURCES = {
    "spotify",
    "lastfm",
    "listenbrainz",
    "apple_music",
    "spotify_export",
    "apple_export",
    "screenshot",
    "spotify_playlist",
    "youtube_music",
}


class HiddenKeysProvider(Protocol):
    """The slice of ``SongFeedbackService`` (Task B10) the context needs."""

    async def hidden_keys(self, user_id: str) -> set[str]: ...


@dataclass
class ArtistSignal:
    weight: float
    source: str
    name: str


@dataclass
class UserContextV2:
    user_id: str
    artists: dict[str, ArtistSignal] = field(default_factory=dict)
    known_songs: dict[str, float] = field(default_factory=dict)
    known_without_karaoke: list[tuple[str, str, str]] = field(default_factory=list)
    hidden: set[str] = field(default_factory=set)
    # Songs the singer said they would not sing in a voice session: penalised, never "known".
    session_no: set[str] = field(default_factory=set)
    genres: set[str] = field(default_factory=set)
    decades: set[str] = field(default_factory=set)
    energy_pref: Literal["chill", "medium", "high"] | None = None
    crowd_pref: Literal["hits", "deep_cuts", "any"] | None = None

    @property
    def has_profile(self) -> bool:
        return bool(self.artists or self.known_songs or self.genres or self.decades)

    @property
    def is_cold(self) -> bool:
        return not self.artists and not self.known_songs


def _add_artist(artists: dict[str, ArtistSignal], name: str, weight: float, source: str) -> None:
    key = artist_key(name)
    if not key:
        return
    cur = artists.get(key)
    if cur is None or weight > cur.weight:
        artists[key] = ArtistSignal(weight=weight, source=source, name=name)


async def _user_doc(firestore: FirestoreService, user_id: str) -> dict[str, Any]:
    """Guest docs are keyed by user_id; verified users' docs by email hash (query on ``user_id``)."""
    if user_id.startswith("guest_"):
        return await firestore.get_document("decide_users", user_id) or {}
    docs = await firestore.query_documents("decide_users", filters=[("user_id", "==", user_id)], limit=1)
    return docs[0] if docs else {}


async def _all_user_songs(firestore: FirestoreService, user_id: str) -> list[dict[str, Any]]:
    """One unbounded query: the client streams it in batches, and no skipped docs are re-billed."""
    return await firestore.query_documents("user_songs", filters=[("user_id", "==", user_id)])


def _kn_id(song_id: object) -> int | None:
    s = str(song_id or "")
    return int(s) if s.isdigit() else None


def _row_sources(d: dict[str, Any]) -> list[str]:
    """The row's ``sources``; rows written before the field existed have just their ``source``."""
    sources = d.get("sources")
    if isinstance(sources, list):
        return [str(s) for s in sources]
    return [str(d["source"])] if d.get("source") else []


def _base_weight(d: dict[str, Any]) -> float:
    """``song_weight`` of the row's best source: imports keep the first ``source`` and only add to
    ``sources`` (a row a voice session created can later carry real listens). Loved/manual rules hold."""
    return max([w.song_weight(d), *(w.song_weight({**d, "source": s}) for s in _row_sources(d))])


def _map_songs(
    reader: CatalogReader, docs: list[dict[str, Any]]
) -> tuple[dict[str, float], list[tuple[str, str, str]], dict[str, int], set[str]]:
    """Return (song_key -> weight, songs with no karaoke version, song_key -> artist key per song,
    voice-session "would not sing" keys).

    Voice-session labels come from the ``session_*`` fields whatever the row's ``source``: "yes" /
    "yes in another key" and a "fine" feel raise the weight (max wins); "no" puts the song in
    ``session_no`` and out of the known songs. A row whose only source is ``voice_session`` has no
    base weight (known only through a label) and never counts toward its artist."""
    kn_keys = reader.kn_song_keys([i for i in (_kn_id(d.get("song_id")) for d in docs) if i is not None])
    known: dict[str, float] = {}
    no_karaoke: dict[str, tuple[str, str, str]] = {}
    artist_songs: dict[str, set[str]] = {}
    session_no: set[str] = set()
    for d in docs:
        artist, title = d.get("artist") or "", d.get("title") or ""
        kn_id = _kn_id(d.get("song_id"))
        key: str | None = kn_keys.get(kn_id) if kn_id is not None else None
        has_karaoke = key is not None
        if key is None:
            if not artist or not title:
                continue
            key = song_key(artist, title)
            has_karaoke = reader.match_kn(artist, title) is not None or reader.has_karaoke_key(key)
        if d.get("session_would_sing") == "no":
            session_no.add(key)
        weight = max(_base_weight(d), w.session_song_weight(d) or 0.0)
        if weight > 0:  # a voice_session-only row is known only through a session label
            known[key] = max(known.get(key, 0.0), weight)
            if not has_karaoke:
                no_karaoke.setdefault(key, (key, artist, title))
        # quiz seeds 5 songs per liked artist (affinity already covers it); voice-session pool songs probe range
        if artist and d.get("source") != "quiz" and _row_sources(d) != [w.SESSION_SOURCE]:
            artist_songs.setdefault(artist_key(artist), set()).add(key)
    for key in session_no:
        known.pop(key, None)
        no_karaoke.pop(key, None)
    return known, list(no_karaoke.values()), {k: len(v) for k, v in artist_songs.items()}, session_no


async def _hidden(feedback: HiddenKeysProvider, user_id: str) -> set[str]:
    try:
        return set(await feedback.hidden_keys(user_id))
    except Exception:
        logger.warning("hidden_keys failed for %s; continuing with none hidden", user_id, exc_info=True)
        return set()


async def build_context(
    firestore: FirestoreService, reader: CatalogReader, feedback: HiddenKeysProvider, user_id: str
) -> UserContextV2:
    user, song_docs, artist_docs, hidden = await asyncio.gather(
        _user_doc(firestore, user_id),
        _all_user_songs(firestore, user_id),
        firestore.query_documents("user_artists", filters=[("user_id", "==", user_id)]),
        _hidden(feedback, user_id),
    )
    ctx = UserContextV2(user_id=user_id, hidden=set(hidden))

    # catalog.db reads are synchronous SQLite; keep them off the event loop.
    ctx.known_songs, ctx.known_without_karaoke, songs_per_artist, ctx.session_no = await asyncio.to_thread(
        _map_songs, reader, song_docs
    )

    artists = ctx.artists
    names: dict[str, str] = {}
    for d in song_docs:
        if d.get("artist"):
            names.setdefault(artist_key(d["artist"]), d["artist"])
    for key, n in songs_per_artist.items():  # quiz-seeded songs are excluded from the count
        if key:
            _add_artist(artists, names.get(key, key), w.artist_from_songs(n), "songs")

    for d in artist_docs:
        name, source = d.get("artist_name") or "", d.get("source") or ""
        if source in _TOP_SOURCES:
            _add_artist(artists, name, w.artist_top(w.to_int(d.get("rank"))), source)
        elif source == "manual":
            _add_artist(artists, name, w.ARTIST_MANUAL, "manual")

    for a in user.get("quiz_artist_affinities") or []:
        weight = w.QUIZ_AFFINITY.get(a.get("affinity", ""))
        if weight is not None:
            _add_artist(artists, a.get("artist_name") or "", weight, "quiz")
    for a in user.get("quiz_manual_artists") or []:
        name = a.get("artist_name") if isinstance(a, dict) else a
        _add_artist(artists, name or "", w.ARTIST_MANUAL, "quiz_manual")

    for name in user.get("excluded_artists") or []:
        artists.pop(artist_key(name), None)

    ctx.genres = {str(g).lower() for g in user.get("quiz_genres") or []}
    decades = user.get("quiz_decades") or ([user["quiz_decade_pref"]] if user.get("quiz_decade_pref") else [])
    ctx.decades = {str(d) for d in decades}
    ctx.energy_pref = user.get("quiz_energy_pref") or None
    ctx.crowd_pref = user.get("quiz_crowd_pleaser_pref") or None
    return ctx
