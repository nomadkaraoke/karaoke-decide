"""Playlist routes for managing user karaoke playlists.

Provides CRUD operations for playlists and song management
within playlists.
"""

import asyncio
import logging
import sqlite3
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from starlette.requests import Request

from backend.api.deps import CurrentUser, PlaylistServiceDep, get_catalog_store_dep
from backend.i18n import get_locale_from_request, t
from backend.services.catalog_facets import SongFacets, song_facets
from backend.services.catalog_store import CatalogStore
from backend.services.playlist_service import (
    MAX_ITEMS,
    PlaylistAccessDeniedError,
    PlaylistInfo,
    PlaylistItemIn,
    PlaylistNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class PlaylistItemOut(BaseModel):
    """One ordered setlist entry, with catalog facets when the song is catalogued."""

    item_id: str
    song_key: str | None
    artist: str
    title: str
    added_at: str
    facets: SongFacets | None = None


class PlaylistResponse(BaseModel):
    """Playlist information."""

    id: str
    name: str
    description: str | None
    song_ids: list[str]
    song_count: int
    created_at: str
    updated_at: str
    items: list[PlaylistItemOut] = Field(default_factory=list)
    rev: int = 0


class PlaylistsListResponse(BaseModel):
    """Response containing list of playlists."""

    playlists: list[PlaylistResponse]
    total: int


class CreatePlaylistRequest(BaseModel):
    """Request to create a playlist."""

    id: str | None = Field(None, pattern=r"^pl_[A-Za-z0-9-]{8,64}$", description="Client-generated id (idempotent)")
    items: list[PlaylistItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)

    name: str = Field(..., min_length=1, max_length=100, description="Playlist name")
    description: str | None = Field(None, max_length=500, description="Optional description")


class UpdatePlaylistRequest(BaseModel):
    """Request to update a playlist."""

    name: str | None = Field(None, min_length=1, max_length=100, description="New playlist name")
    description: str | None = Field(None, max_length=500, description="New description")


class ReplaceItemsRequest(BaseModel):
    """Replace a playlist's ordered items (last writer wins)."""

    items: list[PlaylistItemIn] = Field(..., max_length=MAX_ITEMS)
    base_rev: int | None = None


class AddSongRequest(BaseModel):
    """Request to add a song to a playlist."""

    song_id: str = Field(..., min_length=1, description="Song ID to add")


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


async def _responses(playlists: list[PlaylistInfo], store: CatalogStore) -> list[PlaylistResponse]:
    """Playlists as responses; item facets come from one catalog query (None while it warms up)."""
    facets: dict[str, SongFacets] = {}
    keys = list({i.song_key for p in playlists for i in p.items if i.song_key})
    reader = store.reader
    if reader is not None and keys:
        try:
            facets = await asyncio.to_thread(song_facets, reader, keys)
        except sqlite3.Error:
            logger.warning("playlists: catalog facets unavailable", exc_info=True)
    return [
        PlaylistResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            song_ids=p.song_ids,
            song_count=p.song_count,
            created_at=p.created_at.isoformat(),
            updated_at=p.updated_at.isoformat(),
            items=[
                PlaylistItemOut(
                    item_id=i.item_id,
                    song_key=i.song_key,
                    artist=i.artist,
                    title=i.title,
                    added_at=i.added_at.isoformat(),
                    facets=facets.get(i.song_key) if i.song_key else None,
                )
                for i in p.items
            ],
            rev=p.rev,
        )
        for p in playlists
    ]


CatalogDep = Annotated[CatalogStore, Depends(get_catalog_store_dep)]


# -----------------------------------------------------------------------------
# List Playlists
# -----------------------------------------------------------------------------


@router.get("", response_model=PlaylistsListResponse)
async def list_playlists(
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
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

    return PlaylistsListResponse(playlists=await _responses(playlists, store), total=len(playlists))


# -----------------------------------------------------------------------------
# Create Playlist
# -----------------------------------------------------------------------------


@router.post("", response_model=PlaylistResponse, status_code=status.HTTP_201_CREATED)
async def create_playlist(
    request_body: CreatePlaylistRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
) -> PlaylistResponse:
    """Create a new playlist.

    Creates an empty playlist with the given name and optional description.
    Songs can be added using the POST /playlists/{id}/songs endpoint.
    """
    playlist = await playlist_service.create_playlist(
        user_id=user.id,
        name=request_body.name,
        description=request_body.description,
        playlist_id=request_body.id,
        items=request_body.items,
    )

    return (await _responses([playlist], store))[0]


# -----------------------------------------------------------------------------
# Get Playlist
# -----------------------------------------------------------------------------


@router.get("/{playlist_id}", response_model=PlaylistResponse)
async def get_playlist(
    playlist_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
    request: Request,
) -> PlaylistResponse:
    """Get a playlist by ID.

    Returns the playlist if the authenticated user owns it.
    """
    locale = get_locale_from_request(request)
    try:
        playlist = await playlist_service.get_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )

    return (await _responses([playlist], store))[0]


# -----------------------------------------------------------------------------
# Update Playlist
# -----------------------------------------------------------------------------


@router.put("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: str,
    request_body: UpdatePlaylistRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
    request: Request,
) -> PlaylistResponse:
    """Update a playlist's metadata.

    Updates the name and/or description of a playlist.
    Only the playlist owner can update it.
    """
    locale = get_locale_from_request(request)
    try:
        playlist = await playlist_service.update_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            name=request_body.name,
            description=request_body.description,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )

    return (await _responses([playlist], store))[0]


# -----------------------------------------------------------------------------
# Delete Playlist
# -----------------------------------------------------------------------------


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    playlist_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    request: Request,
) -> None:
    """Delete a playlist.

    Permanently deletes a playlist. Only the playlist owner can delete it.
    """
    locale = get_locale_from_request(request)
    try:
        await playlist_service.delete_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )


# -----------------------------------------------------------------------------
# Replace Items (ordered setlist)
# -----------------------------------------------------------------------------


@router.put("/{playlist_id}/items", response_model=PlaylistResponse)
async def replace_playlist_items(
    playlist_id: str,
    request_body: ReplaceItemsRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
    request: Request,
) -> PlaylistResponse:
    """Replace the playlist's ordered items. Last writer wins; ``rev`` increments on every write."""
    locale = get_locale_from_request(request)
    try:
        playlist = await playlist_service.replace_items(
            playlist_id=playlist_id,
            user_id=user.id,
            items=request_body.items,
            base_rev=request_body.base_rev,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )

    return (await _responses([playlist], store))[0]


# -----------------------------------------------------------------------------
# Add Song to Playlist
# -----------------------------------------------------------------------------


@router.post("/{playlist_id}/songs", response_model=PlaylistResponse)
async def add_song_to_playlist(
    playlist_id: str,
    request_body: AddSongRequest,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    store: CatalogDep,
    request: Request,
) -> PlaylistResponse:
    """Add a song to a playlist.

    Adds the specified song to the end of the playlist.
    Duplicate songs are ignored (no error, but not added again).
    """
    locale = get_locale_from_request(request)
    try:
        playlist = await playlist_service.add_song_to_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            song_id=request_body.song_id,
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )

    return (await _responses([playlist], store))[0]


# -----------------------------------------------------------------------------
# Remove Song from Playlist
# -----------------------------------------------------------------------------


_SONGS_MARKER = "/songs/"


def _raw_song_key(request: Request, song_id: str) -> str:
    """The exact song key, percent-decoded once from the raw request path.

    Keys contain ``|`` and may contain ``/``; some stacks (Starlette's TestClient) decode the
    path twice, which would corrupt them. The raw path is unambiguous (same approach as
    ``my_songs._song_key``).
    """
    raw = request.scope.get("raw_path")
    if raw:
        text = raw.decode("latin-1").split("?", 1)[0]
        start = text.find(_SONGS_MARKER)
        if start >= 0:
            song_id = unquote(text[start + len(_SONGS_MARKER) :])
    return song_id


@router.delete("/{playlist_id}/songs/{song_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_song_from_playlist(
    playlist_id: str,
    song_id: str,
    user: CurrentUser,
    playlist_service: PlaylistServiceDep,
    request: Request,
) -> None:
    """Remove a song from a playlist.

    Removes the specified song from the playlist.
    If the song is not in the playlist, this is a no-op (no error).
    """
    locale = get_locale_from_request(request)
    try:
        await playlist_service.remove_song_from_playlist(
            playlist_id=playlist_id,
            user_id=user.id,
            song_id=_raw_song_key(request, song_id),
        )
    except PlaylistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "playlists.notFound"),
        )
    except PlaylistAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=t(locale, "playlists.accessDenied"),
        )
