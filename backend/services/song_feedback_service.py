"""Song feedback: "Not for me" (hide) and "Love it" (adds a manual known song).

Both are keyed by the search service's ``song_key`` (which can hold ``/``, ``|``, ``#``,
accents, any script), so Firestore doc ids are a SHA-1 prefix of the key, never the key.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator

from backend.services.firestore_service import FirestoreService

HIDDEN_COLLECTION = "user_hidden_songs"
USER_SONGS_COLLECTION = "user_songs"


def _digest(song_key: str) -> str:
    return hashlib.sha1(song_key.encode()).hexdigest()[:24]  # noqa: S324  (id derivation, not security)


def feedback_doc_id(user_id: str, song_key: str) -> str:
    """``user_hidden_songs`` doc id. Never contains ``/``."""
    return f"{user_id}:{_digest(song_key)}"


def loved_doc_id(user_id: str, song_key: str) -> str:
    """``user_songs`` doc id for a loved song. Never contains ``/``."""
    return f"{user_id}:sk:{_digest(song_key)}"


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
    def __init__(self, firestore: FirestoreService) -> None:
        self._fs = firestore

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
        now = datetime.now(UTC).isoformat()
        await self._fs.set_document(
            USER_SONGS_COLLECTION,
            loved_doc_id(user_id, song_key),
            {
                "user_id": user_id,
                "song_id": song_key,
                "song_key": song_key,
                "source": "manual",
                "artist": artist,
                "title": title,
                "play_count": 0,
                "is_saved": False,
                "created_at": now,
                "updated_at": now,
            },
        )

    async def unlove(self, user_id: str, song_key: str) -> None:
        await self._fs.delete_document(USER_SONGS_COLLECTION, loved_doc_id(user_id, song_key))

    async def _hidden_docs(self, user_id: str) -> list[dict[str, Any]]:
        return await self._fs.query_documents(HIDDEN_COLLECTION, filters=[("user_id", "==", user_id)])

    async def hidden_keys(self, user_id: str) -> set[str]:
        return {d["song_key"] for d in await self._hidden_docs(user_id) if d.get("song_key")}

    async def list_hidden(self, user_id: str) -> list[FeedbackSong]:
        return _to_songs(await self._hidden_docs(user_id))

    async def list_loved(self, user_id: str) -> list[FeedbackSong]:
        docs = await self._fs.query_documents(
            USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id), ("source", "==", "manual")]
        )
        return _to_songs(docs)
