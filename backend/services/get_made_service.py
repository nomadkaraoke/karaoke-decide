"""Get this made (spec 6.9): the user's songs with no karaoke version, topped up with recs ``make_it``.

Candidates are the user's ``user_songs`` rows with no karaoke version (the test recs v2 uses,
``context.row_key``) that are saved (loved), sung (``times_sung > 0``), imported (a listening
source in ``sources``) or voice-session "yes", plus the uncatalogued songs they sang
(``user_karaoke_unresolved``, key ``"u:" + song_key(artist, title)``, else the stored ``norm_key``).
Rows sharing a key merge (max weight, the strongest ``why``). They rank by recs weight (``context.row_weight``), descending, then by
key; the recs v2 ``make_it`` shelf tops the list up to ``MAX_ITEMS``.

Excluded: dismissed keys (``user_make_dismissed/{user_id}:{sha1(key)[:24]}``), survey-"bad"
songs (``survey_no``), voice-session "no" songs (``session_no``) and hidden ("Not for me") songs,
as recs v2 leaves those out of ``make_it``. Dismissing never hides a song from recs.

Identity is the library's: the catalog ``song_key`` when the song is in catalog.db, else
``"u:" + song_key(artist, title)`` with ``song_key`` null.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from backend.services.catalog_facets import song_titles
from backend.services.karaoke_log_service import UNRESOLVED_COLLECTION
from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.context import (
    LISTENING_SOURCES,
    HiddenKeysProvider,
    hidden_keys_or_none,
    row_key,
    row_kn_keys,
    row_sources,
    row_weight,
)
from backend.services.recs_v2.filters import RecFilters
from backend.services.recs_v2.service import RecommendationsV2Response
from backend.services.song_feedback_service import USER_SONGS_COLLECTION, is_loved
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

logger = logging.getLogger(__name__)

DISMISSED_COLLECTION = "user_make_dismissed"
MAX_ITEMS = 30
MAKE_IT_SECTION = "make_it"

Why = Literal["saved", "sung", "imported", "voice", "recommended"]
# A song several rows (or reasons) support shows its strongest reason.
_WHY_RANK: dict[str, int] = {"saved": 0, "sung": 1, "voice": 2, "imported": 3}
_VOICE_YES = frozenset({"yes", "yes_other_key"})


class GetMadeItem(BaseModel):
    key: str
    song_key: str | None = None
    artist: str
    title: str
    why: Why
    weight: float


class GetMadeResponse(BaseModel):
    items: list[GetMadeItem]


def dismissed_doc_id(user_id: str, key: str) -> str:
    """``user_make_dismissed`` doc id: keys hold ``/``, so the id carries their digest."""
    return f"{user_id}:{hashlib.sha1(key.encode()).hexdigest()[:24]}"  # noqa: S324  (id, not security)


class CatalogProvider(Protocol):
    def require(self) -> CatalogReader: ...


class RecsProvider(Protocol):
    async def recommend(self, user: User, f: RecFilters, sort: Any = "best") -> RecommendationsV2Response: ...


def _row_why(d: dict[str, Any]) -> str | None:
    """The row's strongest reason to be listed, or None when it isn't one of the user's songs."""
    if is_loved(d):
        return "saved"
    if (w.to_int(d.get("times_sung")) or 0) > 0:
        return "sung"
    if d.get("session_would_sing") in _VOICE_YES:
        return "voice"
    if LISTENING_SOURCES.intersection(row_sources(d)):
        return "imported"
    return None


class _Cand:
    def __init__(self, key: str, artist: str, title: str, why: str, weight: float) -> None:
        self.key, self.artist, self.title, self.why, self.weight = key, artist, title, why, weight

    def merge(self, artist: str, title: str, why: str, weight: float) -> None:
        self.weight = max(self.weight, weight)
        if _WHY_RANK[why] < _WHY_RANK[self.why]:
            self.why = why
        if not (self.artist and self.title) and artist and title:
            self.artist, self.title = artist, title


def _own_songs(
    reader: CatalogReader, rows: list[dict[str, Any]], unresolved: list[dict[str, Any]]
) -> tuple[list[GetMadeItem], set[str]]:
    """The user's own get-made items (unranked) and the keys to exclude everywhere
    (survey "bad" / session "no"). Synchronous SQLite: run off the event loop."""
    kn_keys = row_kn_keys(reader, rows)
    cands: dict[str, _Cand] = {}
    excluded: set[str] = set()

    def add(key: str, artist: str, title: str, why: str, weight: float) -> None:
        cur = cands.get(key)
        if cur is None:
            cands[key] = _Cand(key, artist, title, why, weight)
        else:
            cur.merge(artist, title, why, weight)

    for d in rows:
        keyed = row_key(reader, d, kn_keys)
        if keyed is None:
            continue
        key, has_karaoke = keyed
        artist, title = d.get("artist") or "", d.get("title") or ""
        if d.get("sung_feel_latest") == "bad" or d.get("session_would_sing") == "no":
            excluded.add(key)
            if artist and title:  # the song's uncatalogued identity (its unresolved sings)
                excluded.add("u:" + make_song_key(artist, title))
        why = _row_why(d)
        if has_karaoke or why is None:
            continue
        add(key, artist, title, why, row_weight(d))

    for d in unresolved:
        times = w.to_int(d.get("times_sung")) or 0
        artist, title = d.get("artist") or "", d.get("title") or ""
        norm = (make_song_key(artist, title) if artist and title else "") or d.get("norm_key") or ""
        if times < 1 or not norm:
            continue
        if reader.has_karaoke_key(norm) or reader.match_kn(artist, title) is not None:
            continue
        add("u:" + norm, artist, title, "sung", w.sung_song_weight({"times_sung": times}) or 0.0)

    catalogued = song_titles(reader, [k for k in cands if not k.startswith("u:")])
    # Final identity: catalogued keys stay; anything else is "u:" + norm (merging an uncatalogued
    # user_songs row with the same song's unresolved sings).
    final: dict[str, tuple[str | None, _Cand]] = {}
    for key, c in cands.items():
        if key in excluded:
            continue
        in_catalog = key in catalogued
        artist, title = c.artist, c.title
        if not (artist and title) and in_catalog:
            artist, title = catalogued[key]
        if not (artist and title):  # never show a key as a name (R4)
            continue
        # the library identity: "u:" + song_key(artist, title), whatever key the row stored
        out_key = key if in_catalog or key.startswith("u:") else "u:" + make_song_key(artist, title)
        if out_key in excluded:
            continue
        cur = final.get(out_key)
        if cur is None:
            final[out_key] = (key if in_catalog else None, _Cand(out_key, artist, title, c.why, c.weight))
        else:
            cur[1].merge(artist, title, c.why, c.weight)
    items = [
        GetMadeItem(key=k, song_key=sk, artist=c.artist, title=c.title, why=c.why, weight=round(c.weight, 4))  # type: ignore[arg-type]
        for k, (sk, c) in final.items()
    ]
    return items, excluded


class GetMadeService:
    def __init__(
        self,
        firestore: Any,
        catalog_store: CatalogProvider,
        recs: RecsProvider,
        hidden: HiddenKeysProvider | None = None,
    ) -> None:
        self._fs = firestore
        self._store = catalog_store
        self._recs = recs
        self._hidden = hidden

    async def list(self, user_id: str) -> GetMadeResponse:
        reader = await asyncio.to_thread(self._store.require)  # CatalogUnavailable -> route 503
        rows, unresolved, dismissed, hidden, recs = await asyncio.gather(
            self._fs.query_documents(USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id)]),
            self._fs.query_documents(UNRESOLVED_COLLECTION, filters=[("user_id", "==", user_id)]),
            self.dismissed_keys(user_id),
            self._hidden_keys(user_id),
            self._make_it(user_id),
        )
        own, excluded = await asyncio.to_thread(_own_songs, reader, rows, unresolved)
        skip = excluded | dismissed | hidden
        own = sorted(
            (i for i in own if i.key not in skip and (i.song_key or "") not in skip),
            key=lambda i: (-i.weight, i.key),
        )
        items = own[:MAX_ITEMS]
        seen = {i.key for i in items}
        for rec in recs:
            if len(items) >= MAX_ITEMS:
                break
            if rec.key in seen or rec.key in skip:
                continue
            seen.add(rec.key)
            items.append(rec)
        return GetMadeResponse(items=items)

    async def _make_it(self, user_id: str) -> builtins.list[GetMadeItem]:  # ``list`` is a method here
        """The recs v2 ``make_it`` shelf as items; a failed recs call degrades to none."""
        try:
            resp = await self._recs.recommend(User(id=user_id), RecFilters(), "best")
        except Exception:
            logger.warning("get-made: recs failed for %s; listing own songs only", user_id, exc_info=True)
            return []
        return [
            GetMadeItem(
                key=s.song_key, song_key=s.song_key, artist=s.artist, title=s.title, why="recommended", weight=s.score
            )
            for sec in resp.sections
            if sec.id == MAKE_IT_SECTION
            for s in sec.songs
            if not s.has_karaoke_version and s.artist and s.title
        ]

    async def _hidden_keys(self, user_id: str) -> set[str]:
        return set() if self._hidden is None else await hidden_keys_or_none(self._hidden, user_id)

    async def dismissed_keys(self, user_id: str) -> set[str]:
        docs = await self._fs.query_documents(DISMISSED_COLLECTION, filters=[("user_id", "==", user_id)])
        return {str(d["key"]) for d in docs if d.get("key")}

    async def dismiss(self, user_id: str, key: str) -> None:
        """Idempotent: a repeat keeps the first ``created_at``."""
        await self._fs.create_document(
            DISMISSED_COLLECTION,
            dismissed_doc_id(user_id, key),
            {"user_id": user_id, "key": key, "created_at": datetime.now(UTC).isoformat()},
        )

    async def undismiss(self, user_id: str, key: str) -> None:
        await self._fs.delete_document(DISMISSED_COLLECTION, dismissed_doc_id(user_id, key))
