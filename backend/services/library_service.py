"""The Me library: one item per song the user has sung, loved or put in a setlist.

Aggregates ``karaoke_log`` (counting records only, as ``karaoke_log_service._counts``), the
user's loves / hides and their setlists, then decorates catalogued songs with catalog facets.
Identity is ``library_key``: the resolved catalog ``song_key``, else ``"u:" + song_key(artist, title)``.
A hidden-only song is not in the library; a hidden song that was also sung is, flagged ``hidden``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from backend.models.karaoke_log import Vibe
from backend.services.catalog_facets import SongFacets, song_facets, song_titles
from backend.services.catalog_store import CatalogStore
from backend.services.karaoke_log_service import LOG_COLLECTION, SUNG_STATES, library_key
from backend.services.playlist_service import PlaylistService
from backend.services.song_feedback_service import SongFeedbackService
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

logger = logging.getLogger(__name__)

Feel = Literal["great", "ok", "bad"]
KeyFeel = Literal["too_low", "right", "too_high"]
VIBES: tuple[Vibe, ...] = get_args(Vibe)


def _zero_feel() -> dict[Feel, int]:
    return {"great": 0, "ok": 0, "bad": 0}


class LibraryItem(BaseModel):
    key: str
    song_key: str | None = None
    artist: str
    title: str
    times_sung: int = 0
    backfill_times: int = 0
    first_sung_at: datetime | None = None
    last_sung_at: datetime | None = None
    feel_counts: dict[Feel, int] = Field(default_factory=_zero_feel)
    latest_feel: Feel | None = None
    latest_key: KeyFeel | None = None
    crowd_pleaser_count: int = 0
    vibes: list[Vibe] = Field(default_factory=list)
    loved: bool = False
    hidden: bool = False
    setlist_ids: list[str] = Field(default_factory=list)
    facets: SongFacets | None = None


class LibraryResponse(BaseModel):
    items: list[LibraryItem]
    etag: str


def _dt(value: Any) -> datetime | None:
    """ISO timestamp or a (possibly partial) ``YYYY[-MM[-DD]]`` date as an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.strip()
        if len(text) == 4 and text.isdigit():
            text += "-01-01"
        elif len(text) == 7 and text[4] == "-":
            text += "-01"
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _record_time(doc: dict[str, Any]) -> datetime | None:
    return _dt(doc.get("sung_at")) or _dt(doc.get("ended_at")) or _dt((doc.get("night") or {}).get("date"))


def _counts(doc: dict[str, Any]) -> bool:
    return doc.get("state") in SUNG_STATES and not doc.get("deleted") and bool(doc.get("artist") and doc.get("title"))


class _Acc:
    """Running aggregate for one library key."""

    def __init__(self, key: str, song_key: str | None, artist: str, title: str) -> None:
        self.item = LibraryItem(key=key, song_key=song_key, artist=artist, title=title)
        self.surveys: list[tuple[datetime, dict[str, Any]]] = []
        self.vibes: set[str] = set()
        self.name_at: datetime | None = None


class LibraryService:
    def __init__(
        self,
        firestore: Any,
        feedback: SongFeedbackService,
        playlists: PlaylistService,
        catalog_store: CatalogStore,
    ) -> None:
        self._fs = firestore
        self._feedback = feedback
        self._playlist_collection = playlists.PLAYLISTS_COLLECTION
        self._store = catalog_store

    async def get_library(self, user_id: str) -> LibraryResponse:
        logs, loved, hidden, playlists = await asyncio.gather(
            self._fs.query_documents(LOG_COLLECTION, filters=[("user_id", "==", user_id)]),
            self._feedback.list_loved(user_id),
            self._feedback.hidden_keys(user_id),
            self._fs.query_documents(self._playlist_collection, filters=[("user_id", "==", user_id)]),
        )
        accs: dict[str, _Acc] = {}

        def acc(key: str, song_key: str | None, artist: str, title: str) -> _Acc:
            a = accs.get(key)
            if a is None:
                a = accs[key] = _Acc(key, song_key, artist, title)
            return a

        for doc in logs:
            if not _counts(doc):
                continue
            self._add_record(acc, doc)

        for song in loved:
            acc(song.song_key, song.song_key, song.artist, song.title).item.loved = True

        for pl in playlists:
            pl_id = str(pl.get("id") or "")
            for key, song_key, artist, title in self._playlist_members(pl):
                a = acc(key, song_key, artist, title)
                if pl_id and pl_id not in a.item.setlist_ids:
                    a.item.setlist_ids.append(pl_id)

        items: list[LibraryItem] = []
        for key in sorted(accs):
            a = accs[key]
            it = a.item
            it.hidden = key in hidden
            if it.hidden and not (it.times_sung or it.loved or it.setlist_ids):
                continue
            self._finish(a)
            items.append(it)

        await self._decorate(items)
        body = json.dumps(
            [i.model_dump(mode="json") for i in items], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return LibraryResponse(items=items, etag=hashlib.sha1(body.encode()).hexdigest())  # noqa: S324 (etag, not security)

    @staticmethod
    def _add_record(acc: Any, doc: dict[str, Any]) -> None:
        key = library_key(doc)
        resolved = doc.get("song_key") if doc.get("song_key_status") == "resolved" and doc.get("song_key") else None
        a: _Acc = acc(key, resolved, doc["artist"], doc["title"])
        it = a.item
        times = max(int(doc.get("times") or 1), 1)
        it.times_sung += times
        backfill = doc.get("origin") == "backfill"
        if backfill:
            it.backfill_times += times
        when = _record_time(doc)
        if when is not None:
            if it.first_sung_at is None or when < it.first_sung_at:
                it.first_sung_at = when
            if it.last_sung_at is None or when > it.last_sung_at:
                it.last_sung_at = when
        # Display names come from the most recent record.
        if when is not None and (a.name_at is None or when >= a.name_at):
            a.name_at = when
            it.artist, it.title = doc["artist"], doc["title"]
        survey = doc.get("survey") or {}
        if survey:
            feel = survey.get("feel")
            if feel in it.feel_counts:
                it.feel_counts[feel] += 1
            if survey.get("crowd_pleaser"):
                it.crowd_pleaser_count += 1
            a.vibes.update(survey.get("vibes") or [])
            answered = _dt(survey.get("answered_at"))
            a.surveys.append((answered or datetime.min.replace(tzinfo=UTC), survey))

    @staticmethod
    def _finish(a: _Acc) -> None:
        a.item.vibes = [v for v in VIBES if v in a.vibes]
        # Latest answer per field: a newer survey that skipped a question doesn't erase an older answer.
        for _, s in sorted(a.surveys, key=lambda t: t[0]):
            if s.get("feel"):
                a.item.latest_feel = s["feel"]
            if s.get("key"):
                a.item.latest_key = s["key"]

    @staticmethod
    def _playlist_members(pl: dict[str, Any]) -> list[tuple[str, str | None, str, str]]:
        """``(library key, song_key, artist, title)`` per setlist entry (``items`` when present, else ``song_ids``)."""
        out: list[tuple[str, str | None, str, str]] = []
        if pl.get("items") is not None:
            for entry in pl["items"]:
                artist, title = entry.get("artist") or "", entry.get("title") or ""
                song_key = entry.get("song_key") or None
                key = song_key or "u:" + make_song_key(artist, title)
                out.append((key, song_key, artist, title))
        else:
            for sid in pl.get("song_ids") or []:
                out.append((str(sid), str(sid), "", ""))
        return out

    async def _decorate(self, items: list[LibraryItem]) -> None:
        """Attach facets (and fill names of key-only setlist songs). Warming up or broken catalog: skip."""
        reader = self._store.reader
        keys = [i.song_key for i in items if i.song_key]
        if reader is None or not keys:
            return
        try:
            facets = await asyncio.to_thread(song_facets, reader, keys)
            nameless = [i.song_key for i in items if i.song_key and not (i.artist and i.title)]
            titles = await asyncio.to_thread(song_titles, reader, nameless) if nameless else {}
        except sqlite3.Error:
            logger.warning("library: catalog facets unavailable", exc_info=True)
            return
        for i in items:
            if i.song_key:
                i.facets = facets.get(i.song_key)
                if not (i.artist and i.title) and i.song_key in titles:
                    i.artist, i.title = titles[i.song_key]
