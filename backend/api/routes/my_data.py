"""My Data routes for user data transparency and management.

Provides endpoints for the My Data page, allowing users to:
- View summary of all data the system knows about them
- Manage their liked artists (add/remove)
- View and edit their preferences (genres, decades, energy)
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentUser, UserDataServiceDep

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class ServiceSummary(BaseModel):
    """Summary of a connected music service."""

    connected: bool
    username: str | None = None
    tracks_synced: int | None = None  # Karaoke-matched tracks only
    songs_synced: int | None = None  # Total unique songs synced (all tracks)
    artists_synced: int | None = None  # Total artists synced
    last_sync_at: str | None = None


class ArtistsSummary(BaseModel):
    """Summary of user's artists."""

    total: int
    by_source: dict[str, int]


class SongsSummary(BaseModel):
    """Summary of user's songs."""

    total: int
    with_karaoke: int
    known_songs: int


class PreferencesSummary(BaseModel):
    """Summary of user's quiz preferences."""

    completed: bool
    decade: str | None = None
    energy: str | None = None
    genres: list[str] = []


class DataSummaryResponse(BaseModel):
    """Response containing full summary of user's data."""

    services: dict[str, ServiceSummary]
    artists: ArtistsSummary
    songs: SongsSummary
    preferences: PreferencesSummary


class UserArtistResponse(BaseModel):
    """An artist from user's data with merged source information.

    MBID-first: MusicBrainz ID is the primary identifier when available.
    Spotify ID is optional enrichment for images and external links.
    """

    # Primary identifier (MusicBrainz)
    mbid: str | None = None  # MusicBrainz artist UUID (primary when available)
    artist_name: str  # Canonical name

    # Source information
    sources: list[str]  # All sources where this artist appears: spotify, lastfm, quiz

    # Spotify-specific data
    spotify_id: str | None = None  # Spotify artist ID (for images, links)
    spotify_rank: int | None = None  # Position in Spotify top artists (1-50)
    spotify_time_range: str | None = None  # short_term, medium_term, long_term
    popularity: int | None = None  # Spotify global popularity score (0-100)
    genres: list[str] = []  # Artist genres from Spotify

    # Last.fm-specific data
    lastfm_rank: int | None = None  # Position in Last.fm top artists
    lastfm_playcount: int | None = None  # Actual listen count from Last.fm

    # MusicBrainz-specific data
    tags: list[str] = []  # MusicBrainz community tags

    # User preferences
    is_excluded: bool = False  # Whether hidden from recommendations
    is_manual: bool = False  # Whether added manually (vs synced)


class AllArtistsResponse(BaseModel):
    """Response containing paginated list of user's artists."""

    artists: list[UserArtistResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class ExcludeArtistResponse(BaseModel):
    """Response after excluding/including an artist."""

    artist_name: str
    excluded: bool
    success: bool


class AddArtistRequest(BaseModel):
    """Request to add an artist manually.

    MBID-first: MusicBrainz ID is the primary identifier when available.
    Spotify ID is optional for backward compatibility and metadata enrichment.
    """

    artist_name: str = Field(..., min_length=1, max_length=200)
    mbid: str | None = Field(None, description="MusicBrainz artist UUID (primary)")
    spotify_artist_id: str | None = Field(None, description="Spotify artist ID for metadata enrichment")


class AddArtistResponse(BaseModel):
    """Response after adding an artist."""

    artists: list[str]
    added: str


class RemoveArtistResponse(BaseModel):
    """Response after removing an artist."""

    removed: str
    removed_from: list[str]
    success: bool


class PreferencesResponse(BaseModel):
    """User's quiz preferences."""

    decade_preference: str | None = None
    energy_preference: Literal["chill", "medium", "high"] | None = None
    genres: list[str] = []


class UpdatePreferencesRequest(BaseModel):
    """Request to update preferences."""

    decade_preference: str | None = None
    energy_preference: Literal["chill", "medium", "high"] | None = None
    genres: list[str] | None = None


# -----------------------------------------------------------------------------
# Data Summary
# -----------------------------------------------------------------------------


@router.get("/summary", response_model=DataSummaryResponse)
async def get_data_summary(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
) -> DataSummaryResponse:
    """Get summary of all user data for My Data page.

    Returns aggregated counts and status for:
    - Connected music services (Spotify, Last.fm)
    - Artists by source (Spotify, Last.fm, Quiz, Manual)
    - Songs total and with karaoke versions
    - Quiz/preferences completion status
    """
    summary = await user_data_service.get_data_summary(user.id)

    return DataSummaryResponse(
        services={k: ServiceSummary(**v) for k, v in summary["services"].items()},
        artists=ArtistsSummary(**summary["artists"]),
        songs=SongsSummary(**summary["songs"]),
        preferences=PreferencesSummary(**summary["preferences"]),
    )


# -----------------------------------------------------------------------------
# Artists Management
# -----------------------------------------------------------------------------


@router.get("/artists", response_model=AllArtistsResponse)
async def get_all_artists(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(100, ge=1, le=500, description="Artists per page"),
) -> AllArtistsResponse:
    """Get all artists for user from all sources with pagination.

    Returns combined and merged list from:
    - Synced artists (Spotify, Last.fm) - merged when same artist appears in both
    - Quiz-selected artists
    - Manually added artists

    Each artist includes:
    - sources: List of where this artist was found (spotify, lastfm, quiz)
    - Source-specific stats (Spotify rank/popularity, Last.fm playcount)
    - is_excluded: Whether hidden from recommendations
    - is_manual: Whether added manually (can be deleted)

    Sorted by: playcount (desc), best rank (asc), number of sources (desc)
    """
    result = await user_data_service.get_all_artists(user.id, page=page, per_page=per_page)

    return AllArtistsResponse(
        artists=[UserArtistResponse(**a) for a in result["artists"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        has_more=result["has_more"],
    )


@router.post("/artists", response_model=AddArtistResponse, status_code=status.HTTP_201_CREATED)
async def add_artist(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    request: AddArtistRequest,
) -> AddArtistResponse:
    """Add an artist manually to user's preferences.

    The artist will be stored in the same list as quiz-selected artists
    and used in the recommendation engine.

    If spotify_artist_id is provided (from autocomplete), the artist will
    be stored with Spotify metadata (genres, popularity).
    """
    result = await user_data_service.add_artist(
        user.id,
        request.artist_name,
        spotify_artist_id=request.spotify_artist_id,
    )
    return AddArtistResponse(**result)


# NOTE: Exclude routes MUST be defined BEFORE /artists/{artist_name} to avoid
# FastAPI matching "exclude" as a path parameter
@router.post("/artists/exclude", response_model=ExcludeArtistResponse)
async def exclude_artist(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    artist_name: str = Query(..., description="Artist name to exclude"),
) -> ExcludeArtistResponse:
    """Exclude an artist from recommendations.

    This is a soft hide - the artist remains in your data but won't
    be used when generating recommendations. This persists through
    re-syncs from Spotify/Last.fm.

    Use this when you like an artist but don't want to sing their songs.
    """
    result = await user_data_service.exclude_artist(user.id, artist_name)
    return ExcludeArtistResponse(**result)


@router.delete("/artists/exclude", response_model=ExcludeArtistResponse)
async def include_artist(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    artist_name: str = Query(..., description="Artist name to include"),
) -> ExcludeArtistResponse:
    """Remove an artist from exclusions (un-hide).

    The artist will again be used when generating recommendations.
    """
    result = await user_data_service.include_artist(user.id, artist_name)
    return ExcludeArtistResponse(**result)


@router.delete("/artists/{artist_name}", response_model=RemoveArtistResponse)
async def remove_artist(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    artist_name: str,
) -> RemoveArtistResponse:
    """Remove an artist from user's preferences.

    Removes the artist from all sources:
    - Quiz/manual artist list
    - Synced artists from Spotify/Last.fm
    """
    result = await user_data_service.remove_artist(user.id, artist_name)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artist '{artist_name}' not found in user's data",
        )

    return RemoveArtistResponse(**result)


# -----------------------------------------------------------------------------
# Preferences Management
# -----------------------------------------------------------------------------


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
) -> PreferencesResponse:
    """Get user's quiz/preference settings.

    Returns:
    - decade_preference: Preferred decade for recommendations
    - energy_preference: chill, medium, or high
    - genres: List of preferred genre IDs
    """
    prefs = await user_data_service.get_preferences(user.id)
    return PreferencesResponse(**prefs)


@router.put("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    user: CurrentUser,
    user_data_service: UserDataServiceDep,
    request: UpdatePreferencesRequest,
) -> PreferencesResponse:
    """Update user's quiz/preference settings.

    Only updates fields that are provided in the request.
    Changes take effect on next recommendation request.
    """
    updated = await user_data_service.update_preferences(
        user_id=user.id,
        decade_preference=request.decade_preference,
        energy_preference=request.energy_preference,
        genres=request.genres,
    )
    return PreferencesResponse(**updated)
