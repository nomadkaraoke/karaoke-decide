"""Playlist routes for managing user karaoke playlists.

Provides CRUD operations for playlists and song management
within playlists.
"""

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentUser, PlaylistServiceDep
from backend.services.playlist_service import (
    PlaylistAccessDeniedError,
    PlaylistNotFoundError,
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class PlaylistResponse(BaseModel):
    """Playlist information."""

    id: str
    name: str
    description: str | None
    song_ids: list[str]
    song_count: int
    created_at: str
    updated_at: str


class PlaylistsListResponse(BaseModel):
    """Response containing list of playlists."""

    playlists: list[PlaylistResponse]
    total: int


class CreatePlaylistRequest(BaseModel):
    """Request to create a playlist."""

    name: str = Field(..., min_length=1, max_length=100, description="Playlist name")
    description: str | None = Field(None, max_length=500, description="Optional description")


class UpdatePlaylistRequest(BaseModel):
    """Request to update a playlist."""

    name: str | None = Field(None, min_length=1, max_length=100, description="New playlist name")
    description: str | None = Field(None, max_length=500, description="New description")


class AddSongRequest(BaseModel):
    """Request to add a song to a playlist."""

    song_id: str = Field(..., min_length=1, description="Song ID to add")


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


# -----------------------------------------------------------------------------
# List Playlists
# -----------------------------------------------------------------------------


@router.get("", response_model=PlaylistsListResponse)
async def list_playlists(
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    limit: int = Query(50, ge=1, le=100, description="Maximum playlists to return"),
    offset: int = Query(0, ge=0, description="Number of playlists to skip"),
) -> PlaylistsListResponse:
    """List user's playlists.

    Returns all playlists owned by the authenticated user,
    sorted by most recently updated.
    """
    playlists = await playlist_service.list_playlists(
        user_id=user.id,
        limit=limit,
        offset=offset,
    )

    return PlaylistsListResponse(
        playlists=[
            PlaylistResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                song_ids=p.song_ids,
                song_count=p.song_count,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat(),
            )
            for p in playlists
        ],
        total=len(playlists),
    )


# -----------------------------------------------------------------------------
# Create Playlist
# -----------------------------------------------------------------------------


@router.post("", response_model=PlaylistResponse, status_code=status.HTTP_201_CREATED)
async def create_playlist(
    request: CreatePlaylistRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> PlaylistResponse:
    """Create a new playlist.

    Creates an empty playlist with the given name and optional description.
    Songs can be added using the POST /playlists/{id}/songs endpoint.
    """
    playlist = await playlist_service.create_playlist(
        user_id=user.id,
        name=request.name,
        description=request.description,
    )

    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        song_ids=playlist.song_ids,
        song_count=playlist.song_count,
        created_at=playlist.created_at.isoformat(),
        updated_at=playlist.updated_at.isoformat(),
    )


# -----------------------------------------------------------------------------
# Get Playlist
# -----------------------------------------------------------------------------


@router.get("/{playlist_id}", response_model=PlaylistResponse)
async def get_playlist(
    playlist_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> PlaylistResponse:
    """Get a playlist by ID.

    Returns the playlist if the authenticated user owns it.
    """
    try:
        playlist = await playlist_service.get_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this playlist",
        )

    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        song_ids=playlist.song_ids,
        song_count=playlist.song_count,
        created_at=playlist.created_at.isoformat(),
        updated_at=playlist.updated_at.isoformat(),
    )


# -----------------------------------------------------------------------------
# Update Playlist
# -----------------------------------------------------------------------------


@router.put("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: str,
    request: UpdatePlaylistRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> PlaylistResponse:
    """Update a playlist's metadata.

    Updates the name and/or description of a playlist.
    Only the playlist owner can update it.
    """
    try:
        playlist = await playlist_service.update_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            name=request.name,
            description=request.description,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this playlist",
        )

    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        song_ids=playlist.song_ids,
        song_count=playlist.song_count,
        created_at=playlist.created_at.isoformat(),
        updated_at=playlist.updated_at.isoformat(),
    )


# -----------------------------------------------------------------------------
# Delete Playlist
# -----------------------------------------------------------------------------


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    playlist_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> None:
    """Delete a playlist.

    Permanently deletes a playlist. Only the playlist owner can delete it.
    """
    try:
        await playlist_service.delete_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this playlist",
        )


# -----------------------------------------------------------------------------
# Add Song to Playlist
# -----------------------------------------------------------------------------


@router.post("/{playlist_id}/songs", response_model=PlaylistResponse)
async def add_song_to_playlist(
    playlist_id: str,
    request: AddSongRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> PlaylistResponse:
    """Add a song to a playlist.

    Adds the specified song to the end of the playlist.
    Duplicate songs are ignored (no error, but not added again).
    """
    try:
        playlist = await playlist_service.add_song_to_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            song_id=request.song_id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this playlist",
        )

    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        song_ids=playlist.song_ids,
        song_count=playlist.song_count,
        created_at=playlist.created_at.isoformat(),
        updated_at=playlist.updated_at.isoformat(),
    )


# -----------------------------------------------------------------------------
# Remove Song from Playlist
# -----------------------------------------------------------------------------


@router.delete("/{playlist_id}/songs/{song_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_song_from_playlist(
    playlist_id: str,
    song_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
) -> None:
    """Remove a song from a playlist.

    Removes the specified song from the playlist.
    If the song is not in the playlist, this is a no-op (no error).
    """
    try:
        await playlist_service.remove_song_from_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            song_id=song_id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playlist not found",
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this playlist",
        )
