"""Service for playlist management.

Handles CRUD operations for user-created karaoke playlists
stored in Firestore.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService


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


@dataclass
class PlaylistSong:
    """Song in a playlist with denormalized info."""

    song_id: str
    artist: str
    title: str
    brand_count: int
    popularity: int


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
    USER_SONGS_COLLECTION = "user_songs"

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
    ):
        """Initialize the playlist service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for persistence.
        """
        self.settings = settings
        self.firestore = firestore

    async def create_playlist(
        self,
        user_id: str,
        name: str,
        description: str | None = None,
    ) -> PlaylistInfo:
        """Create a new playlist.

        Args:
            user_id: Owner's user ID.
            name: Playlist name.
            description: Optional description.

        Returns:
            Created playlist info.
        """
        now = datetime.now(UTC)
        playlist_id = str(uuid.uuid4())

        playlist_data: dict[str, Any] = {
            "id": playlist_id,
            "user_id": user_id,
            "name": name,
            "description": description,
            "song_ids": [],
            "song_count": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        await self.firestore.set_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
            playlist_data,
        )

        return PlaylistInfo(
            id=playlist_id,
            user_id=user_id,
            name=name,
            description=description,
            song_ids=[],
            song_count=0,
            created_at=now,
            updated_at=now,
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

        return self._doc_to_playlist(doc)

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

        return [self._doc_to_playlist(doc) for doc in docs]

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
        # Get existing to verify ownership
        playlist = await self.get_playlist(playlist_id, user_id)

        now = datetime.now(UTC)
        update_data: dict = {"updated_at": now.isoformat()}

        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description

        await self.firestore.update_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
            update_data,
        )

        # Return updated playlist
        return PlaylistInfo(
            id=playlist.id,
            user_id=playlist.user_id,
            name=name if name is not None else playlist.name,
            description=description if description is not None else playlist.description,
            song_ids=playlist.song_ids,
            song_count=playlist.song_count,
            created_at=playlist.created_at,
            updated_at=now,
        )

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
        playlist = await self.get_playlist(playlist_id, user_id)

        # Don't add duplicates
        if song_id in playlist.song_ids:
            return playlist

        new_song_ids = playlist.song_ids + [song_id]
        now = datetime.now(UTC)

        await self.firestore.update_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
            {
                "song_ids": new_song_ids,
                "song_count": len(new_song_ids),
                "updated_at": now.isoformat(),
            },
        )

        return PlaylistInfo(
            id=playlist.id,
            user_id=playlist.user_id,
            name=playlist.name,
            description=playlist.description,
            song_ids=new_song_ids,
            song_count=len(new_song_ids),
            created_at=playlist.created_at,
            updated_at=now,
        )

    async def remove_song_from_playlist(
        self,
        playlist_id: str,
        user_id: str,
        song_id: str,
    ) -> PlaylistInfo:
        """Remove a song from a playlist.

        Args:
            playlist_id: Playlist ID.
            user_id: User ID for access check.
            song_id: Song ID to remove.

        Returns:
            Updated playlist info.

        Raises:
            PlaylistNotFoundError: If playlist doesn't exist.
            PlaylistAccessDeniedError: If user doesn't own the playlist.
        """
        playlist = await self.get_playlist(playlist_id, user_id)

        if song_id not in playlist.song_ids:
            return playlist

        new_song_ids = [sid for sid in playlist.song_ids if sid != song_id]
        now = datetime.now(UTC)

        await self.firestore.update_document(
            self.PLAYLISTS_COLLECTION,
            playlist_id,
            {
                "song_ids": new_song_ids,
                "song_count": len(new_song_ids),
                "updated_at": now.isoformat(),
            },
        )

        return PlaylistInfo(
            id=playlist.id,
            user_id=playlist.user_id,
            name=playlist.name,
            description=playlist.description,
            song_ids=new_song_ids,
            song_count=len(new_song_ids),
            created_at=playlist.created_at,
            updated_at=now,
        )

    def _doc_to_playlist(self, doc: dict) -> PlaylistInfo:
        """Convert Firestore document to PlaylistInfo.

        Args:
            doc: Firestore document dict.

        Returns:
            PlaylistInfo instance.
        """
        return PlaylistInfo(
            id=doc["id"],
            user_id=doc["user_id"],
            name=doc["name"],
            description=doc.get("description"),
            song_ids=doc.get("song_ids", []),
            song_count=doc.get("song_count", 0),
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
        )


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
