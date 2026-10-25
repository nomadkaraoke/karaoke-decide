"""Service for playlist management.

Handles CRUD operations for user-created karaoke playlists
stored in Firestore.
"""

import asyncio
import logging
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from backend.config import BackendSettings
from backend.services.catalog_facets import song_titles
from backend.services.catalog_store import CatalogStore, get_catalog_store
from backend.services.firestore_service import FirestoreService

logger = logging.getLogger(__name__)

MAX_ITEMS = 500


class PlaylistItemIn(BaseModel):
    """A setlist entry as sent by a client."""

    item_id: str | None = Field(default=None, max_length=64)
    song_key: str | None = Field(default=None, max_length=600)
    artist: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _names_or_key(self) -> "PlaylistItemIn":
        # Legacy catalogued entries may have empty names; uncatalogued ones need both.
        if not self.song_key and not (self.artist and self.title):
            raise ValueError("artist and title are required when song_key is empty")
        return self


@dataclass
class PlaylistItem:
    """A stored setlist entry."""

    item_id: str
    song_key: str | None
    artist: str
    title: str
    added_at: datetime


@dataclass
class PlaylistInfo:
    """Playlist information."""

    id: str
    user_id: str
    name: str
    description: str | None
    song_ids: list[str]
    song_count: int
    created_at: datetime
    updated_at: datetime
    items: list[PlaylistItem] = field(default_factory=list)
    rev: int = 0


class PlaylistNotFoundError(Exception):
    """Raised when a playlist is not found."""

    pass


class PlaylistAccessDeniedError(Exception):
    """Raised when user doesn't have access to a playlist."""

    pass


class PlaylistService:
    """Service for playlist management.

    Handles:
    - Creating, reading, updating, deleting playlists
    - Adding and removing songs from playlists
    - Listing user's playlists
    """

    PLAYLISTS_COLLECTION = "playlists"

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        catalog_store: CatalogStore | None = None,
    ):
        """Initialize the playlist service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for persistence.
        """
        self.settings = settings
        self.firestore = firestore
        self._catalog_store = catalog_store

    # ------------------------------------------------------------------ helpers

    async def _titles(self, keys: list[str]) -> dict[str, tuple[str, str]]:
        """``song_key -> (artist, title)``; empty while the catalog warms up or on SQLite errors."""
        store = self._catalog_store or get_catalog_store()
        reader = store.reader
        if reader is None or not keys:
            return {}
        try:
            return await asyncio.to_thread(song_titles, reader, keys)
        except sqlite3.Error:
            logger.warning("playlists: catalog titles unavailable", exc_info=True)
            return {}

    @staticmethod
    def _legacy_keys(docs: list[dict[str, Any]]) -> list[str]:
        """Song keys whose names need a catalog lookup: ``song_ids`` of legacy docs, nameless stored items."""
        keys: list[str] = []
        for d in docs:
            if d.get("items") is None:
                keys.extend(str(k) for k in d.get("song_ids") or [])
            else:
                keys.extend(
                    e["song_key"] for e in d["items"] if e.get("song_key") and not (e.get("artist") and e.get("title"))
                )
        return keys

    @staticmethod
    def _stored_item(item: PlaylistItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "song_key": item.song_key,
            "artist": item.artist,
            "title": item.title,
            "added_at": item.added_at.isoformat(),
        }

    @staticmethod
    def _items_from_doc(
        doc: dict[str, Any], titles: dict[str, tuple[str, str]], fallback: bool = True
    ) -> list[PlaylistItem]:
        """Items of a doc (legacy ``song_ids`` docs are upgraded in memory).

        With ``fallback`` empty names are filled from the catalog ``titles`` (real names only). If the
        catalog cannot name the song they stay empty: an id is never substituted for a name, so clients
        display ``song_key`` themselves. Persisting paths pass ``fallback=False``.
        """

        def named(song_key: str | None, artist: str, title: str) -> tuple[str, str]:
            if song_key and not (artist and title):
                known = titles.get(song_key) if fallback else None
                if known:
                    return known
            return artist, title

        if doc.get("items") is not None:
            out: list[PlaylistItem] = []
            for e in doc["items"]:
                song_key = e.get("song_key") or None
                artist, title = named(song_key, e.get("artist") or "", e.get("title") or "")
                out.append(
                    PlaylistItem(
                        item_id=e["item_id"],
                        song_key=song_key,
                        artist=artist,
                        title=title,
                        added_at=datetime.fromisoformat(e["added_at"]),
                    )
                )
            return out
        added = datetime.fromisoformat(doc.get("updated_at") or doc["created_at"])
        legacy: list[PlaylistItem] = []
        for n, sid in enumerate(doc.get("song_ids") or []):
            sid = str(sid)
            artist, title = named(sid, "", "")
            legacy.append(PlaylistItem(item_id=f"legacy-{n}", song_key=sid, artist=artist, title=title, added_at=added))
        return legacy

    @staticmethod
    def _build_items(items_in: list[PlaylistItemIn], existing: list[PlaylistItem], now: datetime) -> list[PlaylistItem]:
        prior = {i.item_id: i for i in existing}
        seen: set[str] = set()
        out: list[PlaylistItem] = []
        for it in items_in:
            item_id = it.item_id if it.item_id and it.item_id not in seen else f"it_{uuid.uuid4().hex[:16]}"
            seen.add(item_id)
            old = prior.get(item_id)
            out.append(
                PlaylistItem(
                    item_id=item_id,
                    song_key=it.song_key or None,
                    artist=it.artist,
                    title=it.title,
                    added_at=old.added_at if old else now,
                )
            )
        return out

    def _with_items(self, doc: dict[str, Any], items: list[PlaylistItem], now: datetime) -> dict[str, Any]:
        song_ids = [i.song_key for i in items if i.song_key]
        return {
            **doc,
            "items": [self._stored_item(i) for i in items],
            "song_ids": song_ids,
            "song_count": len(items),
            "rev": int(doc.get("rev") or 0) + 1,
            "updated_at": now.isoformat(),
        }

    async def create_playlist(
        self,
        user_id: str,
        name: str,
        description: str | None = None,
        playlist_id: str | None = None,
        items: list[PlaylistItemIn] | None = None,
    ) -> PlaylistInfo:
        """Create a playlist.

        With a client-supplied ``playlist_id`` the call is idempotent: an existing doc of the
        same user is returned unchanged; another user's raises ``PlaylistAccessDeniedError``.
        """
        items_in = items or []
        if len(items_in) > MAX_ITEMS:
            raise ValueError(f"at most {MAX_ITEMS} items")
        now = datetime.now(UTC)
        new_id = playlist_id or str(uuid.uuid4())
        built = self._build_items(items_in, [], now)
        data: dict[str, Any] = {
            "id": new_id,
            "user_id": user_id,
            "name": name,
            "description": description,
            "song_ids": [i.song_key for i in built if i.song_key],
            "song_count": len(built),
            "items": [self._stored_item(i) for i in built],
            "rev": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        if playlist_id is None:
            await self.firestore.set_document(self.PLAYLISTS_COLLECTION, new_id, data)
            return self._info(data, built)

        async def body(txn: Any) -> dict[str, Any]:
            cur = await txn.get(self.PLAYLISTS_COLLECTION, new_id)
            if cur is not None:
                if cur.get("user_id") != user_id:
                    raise PlaylistAccessDeniedError("Access denied to this playlist")
                return {"id": new_id, **cur}
            txn.set(self.PLAYLISTS_COLLECTION, new_id, data)
            return data

        doc = await self.firestore.run_transaction(body)
        return await self._doc_to_playlist(doc)

    async def replace_items(
        self,
        playlist_id: str,
        user_id: str,
        items: list[PlaylistItemIn],
        base_rev: int | None,
    ) -> PlaylistInfo:
        """Replace the ordered items. Last writer wins: ``base_rev`` is informational, ``rev`` += 1."""
        if len(items) > MAX_ITEMS:
            raise ValueError(f"at most {MAX_ITEMS} items")
        now = datetime.now(UTC)
        pre = await self.firestore.get_document(self.PLAYLISTS_COLLECTION, playlist_id)
        titles = await self._titles(self._legacy_keys([pre])) if pre else {}

        async def body(txn: Any) -> tuple[dict[str, Any], list[PlaylistItem]]:
            cur = await txn.get(self.PLAYLISTS_COLLECTION, playlist_id)
            if cur is None:
                raise PlaylistNotFoundError(f"Playlist {playlist_id} not found")
            if cur.get("user_id") != user_id:
                raise PlaylistAccessDeniedError("Access denied to this playlist")
            built = self._build_items(items, self._items_from_doc(cur, titles, fallback=False), now)
            new = self._with_items({"id": playlist_id, **cur}, built, now)
            txn.set(self.PLAYLISTS_COLLECTION, playlist_id, new)
            return new, built

        doc, built = await self.firestore.run_transaction(body)
        return self._info(doc, built)

    def _info(self, doc: dict[str, Any], items: list[PlaylistItem]) -> PlaylistInfo:
        return PlaylistInfo(
            id=doc["id"],
            user_id=doc["user_id"],
            name=doc["name"],
            description=doc.get("description"),
            song_ids=doc.get("song_ids", []),
            song_count=doc.get("song_count", len(items)),
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
            items=items,
            rev=int(doc.get("rev") or 0),
        )

    async def get_playlist(
        self,
        playlist_id: str,
        user_id: str,
    ) -> PlaylistInfo:
        """Get a playlist by ID.

        Args:
            playlist_id: Playlist ID.
            user_id: User ID for access check.

        Returns:
            Playlist info.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        doc = await self.firestore.get_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
        )

        if doc is None:
            raise PlaylistNotFoundError(f"Playlist {playlist_id} not found")

        if doc["user_id"] != user_id:
            raise PlaylistAccessDeniedError("Access denied to this playlist")

        return await self._doc_to_playlist(doc)

    async def list_playlists(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PlaylistInfo]:
        """List user's playlists.

        Args:
            user_id: User ID.
            limit: Maximum playlists to return.
            offset: Number of playlists to skip.

        Returns:
            List of playlist info.
        """
        docs = await self.firestore.query_documents(
            self.PLAYLISTS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            order_by="updated_at",
            order_direction="DESCENDING",
            limit=limit,
            offset=offset,
        )

        titles = await self._titles(self._legacy_keys(docs))
        return [self._info(d, self._items_from_doc(d, titles)) for d in docs]

    async def update_playlist(
        self,
        playlist_id: str,
        user_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> PlaylistInfo:
        """Update a playlist's metadata.

        Args:
            playlist_id: Playlist ID.
            user_id: User ID for access check.
            name: New name (if provided).
            description: New description (if provided).

        Returns:
            Updated playlist info.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        now = datetime.now(UTC)

        async def body(txn: Any) -> dict[str, Any]:
            cur = await txn.get(self.PLAYLISTS_COLLECTION, playlist_id)
            if cur is None:
                raise PlaylistNotFoundError(f"Playlist {playlist_id} not found")
            if cur.get("user_id") != user_id:
                raise PlaylistAccessDeniedError("Access denied to this playlist")
            new = {**cur, "updated_at": now.isoformat(), "rev": int(cur.get("rev") or 0) + 1}
            if name is not None:
                new["name"] = name
            if description is not None:
                new["description"] = description
            txn.set(self.PLAYLISTS_COLLECTION, playlist_id, new)
            return {"id": playlist_id, **new}

        return await self._doc_to_playlist(await self.firestore.run_transaction(body))

    async def delete_playlist(
        self,
        playlist_id: str,
        user_id: str,
    ) -> None:
        """Delete a playlist.

        Args:
            playlist_id: Playlist ID.
            user_id: User ID for access check.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        # Verify ownership first
        await self.get_playlist(playlist_id, user_id)

        await self.firestore.delete_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
        )

    async def add_song_to_playlist(
        self,
        playlist_id: str,
        user_id: str,
        song_id: str,
    ) -> PlaylistInfo:
        """Add a song to a playlist.

        Args:
            playlist_id: Playlist ID.
            user_id: User ID for access check.
            song_id: Song ID to add.

        Returns:
            Updated playlist info.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        pre = await self.firestore.get_document(self.PLAYLISTS_COLLECTION, playlist_id)
        keys = (self._legacy_keys([pre]) if pre else []) + [song_id]
        titles = await self._titles(keys)
        now = datetime.now(UTC)

        def build(items: list[PlaylistItem]) -> list[PlaylistItem] | None:
            if any(i.song_key == song_id for i in items):  # no duplicates
                return None
            if len(items) >= MAX_ITEMS:
                raise ValueError(f"at most {MAX_ITEMS} items")
            artist, title = titles.get(song_id, ("", ""))  # never persist the id as a title
            new = PlaylistItem(
                item_id=f"it_{uuid.uuid4().hex[:16]}", song_key=song_id, artist=artist, title=title, added_at=now
            )
            return [*items, new]

        return await self._mutate_items(playlist_id, user_id, titles, now, build)

    async def _mutate_items(
        self,
        playlist_id: str,
        user_id: str,
        titles: dict[str, tuple[str, str]],
        now: datetime,
        build: Callable[[list[PlaylistItem]], list[PlaylistItem] | None],
    ) -> PlaylistInfo:
        """Transactional read-modify-write of the items (``build`` returns None for no change)."""

        async def body(txn: Any) -> dict[str, Any]:
            cur = await txn.get(self.PLAYLISTS_COLLECTION, playlist_id)
            if cur is None:
                raise PlaylistNotFoundError(f"Playlist {playlist_id} not found")
            if cur.get("user_id") != user_id:
                raise PlaylistAccessDeniedError("Access denied to this playlist")
            doc = {"id": playlist_id, **cur}
            new_items = build(self._items_from_doc(doc, titles, fallback=False))
            if new_items is None:
                return doc
            new = self._with_items(doc, new_items, now)
            txn.set(self.PLAYLISTS_COLLECTION, playlist_id, new)
            return new

        return await self._doc_to_playlist(await self.firestore.run_transaction(body))

    async def remove_song_from_playlist(
        self,
        playlist_id: str,
        user_id: str,
        song_id: str,
    ) -> PlaylistInfo:
        """Remove a song from a playlist.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        pre = await self.firestore.get_document(self.PLAYLISTS_COLLECTION, playlist_id)
        titles = await self._titles(self._legacy_keys([pre])) if pre else {}

        def build(items: list[PlaylistItem]) -> list[PlaylistItem] | None:
            kept = [i for i in items if i.song_key != song_id]
            return kept if len(kept) != len(items) else None

        return await self._mutate_items(playlist_id, user_id, titles, datetime.now(UTC), build)

    async def _doc_to_playlist(self, doc: dict) -> PlaylistInfo:
        """Firestore doc -> PlaylistInfo. Legacy docs (``song_ids`` only) get ``items`` in memory."""
        titles = await self._titles(self._legacy_keys([doc]))
        return self._info(doc, self._items_from_doc(doc, titles))


# Lazy initialization
_playlist_service: PlaylistService | None = None


def get_playlist_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> PlaylistService:
    """Get the playlist service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.

    Returns:
        PlaylistService instance.
    """
    global _playlist_service

    if _playlist_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)

        _playlist_service = PlaylistService(settings, firestore)

    return _playlist_service
