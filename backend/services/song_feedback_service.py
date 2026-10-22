"""Song feedback: "Not for me" (hide) and "Love it" (marks a known song as loved).

Both are keyed by the search service's ``song_key`` (which can hold ``/``, ``|``, ``#``,
accents, any script), so Firestore doc ids are a SHA-1 prefix of the key, never the key.

"Love it" shares its ``user_songs`` row with the karaoke log (``choose_user_song_row``): the
``{user_id}:{kn_id}`` row when catalog.db maps the key to a KaraokeNerds id and that row
exists, else ``loved_doc_id``. Love merges a ``loved`` marker into the row (sung history and
the row's source survive); unlove clears the marker and deletes the row only when nothing
else is on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, field_validator

from backend.services.catalog_store import get_catalog_store
from backend.services.firestore_service import FirestoreService

logger = logging.getLogger(__name__)

HIDDEN_COLLECTION = "user_hidden_songs"
USER_SONGS_COLLECTION = "user_songs"
# Sources whose rows exist only because of love or the karaoke log: unlove may delete them
# once they hold no sung history. Every other source (quiz, sync, known songs) keeps its row.
_DELETABLE_SOURCES = frozenset({"manual", "karaoke_log"})

DocGetter = Callable[[str, str], Awaitable[dict[str, Any] | None]]
KnIdLookup = Callable[[str], int | None]


class KnIdReader(Protocol):
    def kn_ids_for_song_keys(self, keys: list[str]) -> dict[str, int]: ...


def _digest(song_key: str) -> str:
    return hashlib.sha1(song_key.encode()).hexdigest()[:24]  # noqa: S324  (id derivation, not security)


def feedback_doc_id(user_id: str, song_key: str) -> str:
    """``user_hidden_songs`` doc id. Never contains ``/``."""
    return f"{user_id}:{_digest(song_key)}"


def loved_doc_id(user_id: str, song_key: str) -> str:
    """``user_songs`` doc id for a loved song. Never contains ``/``."""
    return f"{user_id}:sk:{_digest(song_key)}"


def kn_id_for_song_key(reader: KnIdReader | None, song_key: str) -> int | None:
    """The KaraokeNerds id catalog.db maps ``song_key`` to; None while warming up or on error."""
    if reader is None:
        return None
    try:
        return reader.kn_ids_for_song_keys([song_key]).get(song_key)
    except Exception:
        logger.warning("kn_ids_for_song_keys failed; using the song_key row", exc_info=True)
        return None


def _catalog_kn_id(song_key: str) -> int | None:
    return kn_id_for_song_key(get_catalog_store().reader, song_key)


async def choose_user_song_row(
    user_id: str, song_key: str, kn_id: int | None, get: DocGetter
) -> tuple[str, dict[str, Any] | None]:
    """``(doc_id, existing doc or None)`` of the ``user_songs`` row for ``song_key``.

    The ``{user_id}:{kn_id}`` row when ``kn_id`` is known and that row exists (a synced or quiz
    row for the same song), else ``loved_doc_id``. Shared by love and the karaoke log so both
    land on one row. ``get`` is a document read (plain or transactional)."""
    if kn_id is not None:
        kn_doc_id = f"{user_id}:{kn_id}"
        doc = await get(USER_SONGS_COLLECTION, kn_doc_id)
        if doc is not None:
            return kn_doc_id, doc
    doc_id = loved_doc_id(user_id, song_key)
    return doc_id, await get(USER_SONGS_COLLECTION, doc_id)


def is_loved(doc: dict[str, Any]) -> bool:
    """Rows written before the ``loved`` marker existed are loved when their source is manual."""
    loved = doc.get("loved")
    return loved is True or (loved is None and doc.get("source") == "manual")


class FeedbackSong(BaseModel):
    song_key: str
    artist: str
    title: str
    created_at: datetime | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, v: Any) -> Any:
        """Docs hold ISO strings; tolerate datetimes written by older versions."""
        return datetime.fromisoformat(v) if isinstance(v, str) else v


def _to_songs(docs: list[dict[str, Any]]) -> list[FeedbackSong]:
    songs = [
        FeedbackSong(
            song_key=d["song_key"],
            artist=d.get("artist") or "",
            title=d.get("title") or "",
            created_at=d.get("created_at"),
        )
        for d in docs
        if d.get("song_key")
    ]
    songs.sort(key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return songs


class SongFeedbackService:
    def __init__(self, firestore: FirestoreService, kn_id_for: KnIdLookup | None = None) -> None:
        """``kn_id_for`` maps a song_key to its KaraokeNerds id (default: catalog.db)."""
        self._fs = firestore
        self._kn_id_for = kn_id_for or _catalog_kn_id

    async def _row(self, user_id: str, song_key: str) -> tuple[str, dict[str, Any] | None]:
        kn_id = await asyncio.to_thread(self._kn_id_for, song_key)  # SQLite: off the event loop
        return await choose_user_song_row(user_id, song_key, kn_id, self._fs.get_document)

    async def hide(self, user_id: str, song_key: str, artist: str, title: str) -> None:
        await self._fs.set_document(
            HIDDEN_COLLECTION,
            feedback_doc_id(user_id, song_key),
            {
                "user_id": user_id,
                "song_key": song_key,
                "artist": artist,
                "title": title,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    async def unhide(self, user_id: str, song_key: str) -> None:
        await self._fs.delete_document(HIDDEN_COLLECTION, feedback_doc_id(user_id, song_key))

    async def love(self, user_id: str, song_key: str, artist: str, title: str) -> None:
        """Merge the love marker into the song's row; a new row gets ``source: manual``."""
        now = datetime.now(UTC).isoformat()
        doc_id, existing = await self._row(user_id, song_key)
        data: dict[str, Any] = {
            "user_id": user_id,
            "song_key": song_key,
            "artist": artist,
            "title": title,
            "loved": True,
            "loved_at": now,
            "updated_at": now,
        }
        if existing is None:
            data.update(
                {"song_id": song_key, "source": "manual", "play_count": 0, "is_saved": False, "created_at": now}
            )
        await self._fs.set_document(USER_SONGS_COLLECTION, doc_id, data, merge=True)

    async def unlove(self, user_id: str, song_key: str) -> None:
        """Clear the love marker; delete the row only when nothing else is on it.

        Clears both candidate rows (the kn_id row and ``loved_doc_id``): a song loved before a
        sync created its kn_id row is still un-loved."""
        doc_id, existing = await self._row(user_id, song_key)
        rows = {doc_id: existing}
        fallback = loved_doc_id(user_id, song_key)
        if fallback not in rows:
            rows[fallback] = await self._fs.get_document(USER_SONGS_COLLECTION, fallback)
        now = datetime.now(UTC).isoformat()
        for row_id, doc in rows.items():
            if doc is None or not is_loved(doc):
                continue
            if not doc.get("times_sung") and doc.get("source") in _DELETABLE_SOURCES:
                await self._fs.delete_document(USER_SONGS_COLLECTION, row_id)
            else:
                await self._fs.set_document(
                    USER_SONGS_COLLECTION, row_id, {"loved": False, "loved_at": None, "updated_at": now}, merge=True
                )

    async def _hidden_docs(self, user_id: str) -> list[dict[str, Any]]:
        return await self._fs.query_documents(HIDDEN_COLLECTION, filters=[("user_id", "==", user_id)])

    async def hidden_keys(self, user_id: str) -> set[str]:
        return {d["song_key"] for d in await self._hidden_docs(user_id) if d.get("song_key")}

    async def list_hidden(self, user_id: str) -> list[FeedbackSong]:
        return _to_songs(await self._hidden_docs(user_id))

    async def list_loved(self, user_id: str) -> list[FeedbackSong]:
        """Rows with the love marker, plus legacy ``source: manual`` rows written before it."""
        marked, manual = await asyncio.gather(
            self._fs.query_documents(
                USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id), ("loved", "==", True)]
            ),
            self._fs.query_documents(
                USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id), ("source", "==", "manual")]
            ),
        )
        docs = {d["id"]: d for d in [*marked, *manual] if is_loved(d)}
        return _to_songs(list(docs.values()))
