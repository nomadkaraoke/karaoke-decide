"""RecommendationServiceV2: context -> candidates -> scores -> sections, as an API response.

Context building is async Firestore I/O; catalog access (``store.require`` may retry a load
in-request), candidate gathering and scoring are synchronous SQLite / CPU work and run in
worker threads so the event loop stays free.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from typing import Any, Protocol

from pydantic import BaseModel

from backend.services.firestore_service import FirestoreService
from backend.services.recs_v2.candidates import Candidate, gather_candidates
from backend.services.recs_v2.context import HiddenKeysProvider, UserContextV2, build_context
from backend.services.recs_v2.filters import RecFilters, Sort, filters_applied
from backend.services.recs_v2.scoring import Scored, score
from backend.services.recs_v2.sections import Section, build_sections
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User

PAGE_SIZE = 30
_CURSOR = re.compile(r"^o:(\d{1,9})$")

# English fallbacks for clients without i18n; clients render from reason_type / reason_artist.
REASON_TEXT: dict[str, str] = {
    "known_song": "You know this",
    "known_artist": "From an artist you love",
    "similar_artist": "Like {artist}",
    "genre_match": "Matches your genres",
    "decade_match": "From a decade you like",
    "crowd_pleaser": "Crowd pleaser",
    "make_it": "Make it",
}


class BadCursorError(ValueError):
    """The ``cursor`` query param isn't one we issued."""


class CatalogProvider(Protocol):
    """The slice of ``CatalogStore`` the service uses."""

    @property
    def run(self) -> str | None: ...

    def require(self) -> CatalogReader: ...


class RecKaraoke(BaseModel):
    community: bool
    commercial: bool
    karafun: bool
    brand_count: int


class RecFacets(BaseModel):
    decade: str | None
    genres: list[str]
    mood: str | None
    energy: float | None
    lead_gender: str | None
    duration_ms: int | None
    popularity: int | None
    is_classic: bool
    explicit: bool


class RecSong(BaseModel):
    song_key: str
    artist: str
    title: str
    reason: str
    reason_type: str
    reason_artist: str | None
    score: float
    has_karaoke_version: bool
    karaoke: RecKaraoke
    facets: RecFacets


class RecSection(BaseModel):
    id: str
    title_key: str
    reason_key: str
    title_params: dict[str, str]
    songs: list[RecSong]


class RecommendationsV2Response(BaseModel):
    sections: list[RecSection]
    filters_applied: dict[str, Any]
    next_cursor: str | None
    has_profile: bool
    catalog_run: str


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
    except (binascii.Error, ValueError):
        raise BadCursorError(cursor) from None
    m = _CURSOR.match(raw)
    if not m:
        raise BadCursorError(cursor)
    return int(m.group(1))


def _rec_song(s: Scored) -> RecSong:
    c: Candidate = s.c
    reason = REASON_TEXT[s.reason_type].format(artist=s.reason_artist or c.artist)
    return RecSong(
        song_key=c.song_key,
        artist=c.artist,
        title=c.title,
        reason=reason,
        reason_type=s.reason_type,
        reason_artist=s.reason_artist,
        score=round(s.score, 4),
        has_karaoke_version=c.has_karaoke,
        karaoke=RecKaraoke(
            community=c.community, commercial=c.commercial, karafun=c.karafun, brand_count=c.brand_count
        ),
        facets=RecFacets(
            decade=c.decade,
            genres=list(c.genres),
            mood=c.mood,
            energy=c.energy,
            lead_gender=c.lead_gender,
            duration_ms=c.duration_ms,
            popularity=c.popularity,
            is_classic=c.is_classic,
            explicit=c.explicit,
        ),
    )


def _rank(reader: CatalogReader, ctx: UserContextV2, f: RecFilters, sort: Sort) -> list[Section]:
    scored = [score(c, ctx) for c in gather_candidates(reader, ctx, f)]
    return build_sections(scored, ctx, sort)


class RecommendationServiceV2:
    def __init__(self, firestore: FirestoreService, store: CatalogProvider, feedback: HiddenKeysProvider) -> None:
        self._firestore = firestore
        self._store = store
        self._feedback = feedback

    async def recommend(
        self, user: User, f: RecFilters, sort: Sort = "best", cursor: str | None = None
    ) -> RecommendationsV2Response:
        offset = decode_cursor(cursor)  # cheap; reject before any work
        reader = await asyncio.to_thread(self._store.require)  # CatalogUnavailable -> route 503
        ctx = await build_context(self._firestore, reader, self._feedback, user.id)
        sections = await asyncio.to_thread(_rank, reader, ctx, f, sort)

        next_cursor: str | None = None
        if sort != "best":  # only the flat list pages
            for sec in sections:
                total = len(sec.songs)
                sec.songs = sec.songs[offset : offset + PAGE_SIZE]
                if offset + PAGE_SIZE < total:
                    next_cursor = encode_cursor(offset + PAGE_SIZE)
            sections = [sec for sec in sections if sec.songs]

        return RecommendationsV2Response(
            sections=[
                RecSection(
                    id=sec.id,
                    title_key=sec.title_key,
                    reason_key=sec.reason_key,
                    title_params=sec.title_params,
                    songs=[_rec_song(s) for s in sec.songs],
                )
                for sec in sections
            ],
            filters_applied=filters_applied(f, has_availability=reader.has_availability),
            next_cursor=next_cursor,
            has_profile=ctx.has_profile,
            catalog_run=self._store.run or "",
        )
