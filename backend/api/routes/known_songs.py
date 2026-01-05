"""Routes for managing user's known songs.

Allows users to search for and add songs they already know they like singing,
to improve recommendation quality.
"""

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentUser, KnownSongsServiceDep

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class KnownSongResponse(BaseModel):
    """Response for a single known song."""

    id: str
    song_id: str
    artist: str
    title: str
    source: str
    is_saved: bool
    created_at: str
    updated_at: str


class KnownSongsListResponse(BaseModel):
    """Response for listing known songs."""

    songs: list[KnownSongResponse]
    total: int
    page: int
    per_page: int


class AddKnownSongRequest(BaseModel):
    """Request to add a known song."""

    song_id: int = Field(..., description="Karaoke catalog song ID")


class AddKnownSongResponse(BaseModel):
    """Response after adding a known song."""

    added: bool
    song_id: str
    artist: str
    title: str
    already_existed: bool


class AddSpotifyTrackRequest(BaseModel):
    """Request to add a song via Spotify track ID."""

    track_id: str = Field(..., description="Spotify track ID")


class AddSpotifyTrackResponse(BaseModel):
    """Response after adding a Spotify track."""

    added: bool
    track_id: str
    track_name: str
    artist_name: str
    artist_id: str
    popularity: int
    duration_ms: int
    explicit: bool
    already_existed: bool


class BulkAddKnownSongsRequest(BaseModel):
    """Request to add multiple known songs."""

    song_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of karaoke catalog song IDs",
    )


class BulkAddKnownSongsResponse(BaseModel):
    """Response after bulk adding known songs."""

    added: int
    already_existed: int
    not_found: int
    total_requested: int


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


# -----------------------------------------------------------------------------
# List Known Songs
# -----------------------------------------------------------------------------


@router.get("", response_model=KnownSongsListResponse)
async def list_known_songs(
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
) -> KnownSongsListResponse:
    """List user's manually added known songs.

    Returns songs the user has explicitly added as songs they know
    and like to sing, separate from music service sync or quiz.
    """
    result = await known_songs_service.get_known_songs(
        user_id=user.id,
        page=page,
        per_page=per_page,
    )

    return KnownSongsListResponse(
        songs=[
            KnownSongResponse(
                id=song["id"],
                song_id=song["song_id"],
                artist=song["artist"],
                title=song["title"],
                source=song["source"],
                is_saved=song.get("is_saved", True),
                created_at=song["created_at"],
                updated_at=song["updated_at"],
            )
            for song in result.songs
        ],
        total=result.total,
        page=result.page,
        per_page=result.per_page,
    )


# -----------------------------------------------------------------------------
# Add Known Song
# -----------------------------------------------------------------------------


@router.post("", response_model=AddKnownSongResponse, status_code=status.HTTP_201_CREATED)
async def add_known_song(
    request: AddKnownSongRequest,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> AddKnownSongResponse:
    """Add a song to user's known songs.

    Adds a song from the karaoke catalog to the user's library as a
    known song they like to sing. Use the catalog search endpoint to
    find song IDs.
    """
    try:
        result = await known_songs_service.add_known_song(
            user_id=user.id,
            song_id=request.song_id,
        )

        return AddKnownSongResponse(
            added=result.added,
            song_id=result.song_id,
            artist=result.artist,
            title=result.title,
            already_existed=result.already_existed,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# -----------------------------------------------------------------------------
# Bulk Add Known Songs
# -----------------------------------------------------------------------------


@router.post("/bulk", response_model=BulkAddKnownSongsResponse)
async def bulk_add_known_songs(
    request: BulkAddKnownSongsRequest,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> BulkAddKnownSongsResponse:
    """Add multiple songs to user's known songs.

    Efficiently adds multiple songs at once. Returns counts of
    successfully added, already existing, and not found songs.
    """
    result = await known_songs_service.bulk_add_known_songs(
        user_id=user.id,
        song_ids=request.song_ids,
    )

    return BulkAddKnownSongsResponse(
        added=result["added"],
        already_existed=result["already_existed"],
        not_found=result["not_found"],
        total_requested=result["total_requested"],
    )


# -----------------------------------------------------------------------------
# Add Spotify Track
# -----------------------------------------------------------------------------


@router.post("/spotify", response_model=AddSpotifyTrackResponse, status_code=status.HTTP_201_CREATED)
async def add_spotify_track(
    request: AddSpotifyTrackRequest,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> AddSpotifyTrackResponse:
    """Add a song to user's known songs via Spotify track ID.

    Adds a track from the Spotify catalog to the user's library as a
    known song. Use the catalog track search endpoint to find track IDs.

    Note: These tracks may not have karaoke versions available in the
    karaoke catalog, but can be used for "Create Your Own Karaoke".
    """
    try:
        result = await known_songs_service.add_spotify_track(
            user_id=user.id,
            track_id=request.track_id,
        )

        return AddSpotifyTrackResponse(
            added=result.added,
            track_id=result.track_id,
            track_name=result.track_name,
            artist_name=result.artist_name,
            artist_id=result.artist_id,
            popularity=result.popularity,
            duration_ms=result.duration_ms,
            explicit=result.explicit,
            already_existed=result.already_existed,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# -----------------------------------------------------------------------------
# Remove Spotify Track
# -----------------------------------------------------------------------------


@router.delete("/spotify/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_spotify_track(
    track_id: str,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> None:
    """Remove a Spotify track from user's known songs.

    Only removes tracks that were manually added (source='known_songs').
    Tracks from music services sync cannot be removed this way.
    """
    removed = await known_songs_service.remove_spotify_track(
        user_id=user.id,
        track_id=track_id,
    )

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Track not found in known songs or cannot be removed",
        )


# -----------------------------------------------------------------------------
# Remove Known Song (Karaoke Catalog)
# -----------------------------------------------------------------------------


@router.delete("/{song_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_known_song(
    song_id: int,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> None:
    """Remove a song from user's known songs.

    Only removes songs that were manually added (source='known_songs').
    Songs from music services or quiz cannot be removed this way.
    """
    removed = await known_songs_service.remove_known_song(
        user_id=user.id,
        song_id=song_id,
    )

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Song not found in known songs or cannot be removed",
        )
