"""Music service connection routes.

Handles OAuth flows, service connections, and sync operations.
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend.api.deps import (
    CurrentUser,
    MusicServiceServiceDep,
    Settings,
    SyncServiceDep,
)
from karaoke_decide.core.exceptions import NotFoundError, ValidationError

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class ConnectedServiceResponse(BaseModel):
    """Connected music service information."""

    service_type: str
    service_username: str
    last_sync_at: str | None
    sync_status: str
    sync_error: str | None
    tracks_synced: int


class SpotifyConnectResponse(BaseModel):
    """Response for starting Spotify OAuth."""

    auth_url: str


class LastFmConnectRequest(BaseModel):
    """Request to connect Last.fm account."""

    username: str


class SyncResultItem(BaseModel):
    """Result for a single service sync."""

    service_type: str
    tracks_fetched: int
    tracks_matched: int
    user_songs_created: int
    user_songs_updated: int
    error: str | None


class SyncResultResponse(BaseModel):
    """Response from sync operation."""

    results: list[SyncResultItem]


class SyncStatusResponse(BaseModel):
    """Current sync status across all services."""

    services: list[ConnectedServiceResponse]


class DisconnectResponse(BaseModel):
    """Response after disconnecting a service."""

    message: str


# -----------------------------------------------------------------------------
# List Connected Services
# -----------------------------------------------------------------------------


@router.get("", response_model=list[ConnectedServiceResponse])
async def list_services(
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
) -> list[ConnectedServiceResponse]:
    """List user's connected music services.

    Returns information about each connected service including
    sync status and track counts.
    """
    services = await music_service.get_user_services(user.id)

    return [
        ConnectedServiceResponse(
            service_type=svc.service_type,
            service_username=svc.service_username,
            last_sync_at=svc.last_sync_at.isoformat() if svc.last_sync_at else None,
            sync_status=svc.sync_status,
            sync_error=svc.sync_error,
            tracks_synced=svc.tracks_synced,
        )
        for svc in services
    ]


# -----------------------------------------------------------------------------
# Spotify OAuth Flow
# -----------------------------------------------------------------------------


@router.post("/spotify/connect", response_model=SpotifyConnectResponse)
async def connect_spotify(
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
) -> SpotifyConnectResponse:
    """Start Spotify OAuth flow.

    Returns an authorization URL to redirect the user to Spotify's
    login page. After authorization, Spotify will redirect back to
    the callback endpoint.
    """
    # Create OAuth state for CSRF protection
    state = await music_service.create_oauth_state(user.id, "spotify")

    # Get authorization URL
    auth_url = music_service.get_spotify_auth_url(state)

    return SpotifyConnectResponse(auth_url=auth_url)


@router.get("/spotify/callback")
async def spotify_callback(
    music_service: MusicServiceServiceDep,
    settings: Settings,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> RedirectResponse:
    """Handle Spotify OAuth callback.

    This endpoint is called by Spotify after user authorization.
    It exchanges the code for tokens, stores the connection, and
    redirects to the frontend.

    Note: This endpoint doesn't require authentication because it's
    called by Spotify's redirect, not the user directly. User identity
    is verified through the OAuth state parameter.
    """
    frontend_url = settings.frontend_url

    # Handle OAuth errors
    if error:
        return RedirectResponse(
            url=f"{frontend_url}/services/spotify/error?message={quote(error, safe='')}",
            status_code=status.HTTP_302_FOUND,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_url}/services/spotify/error?message=missing_params",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify OAuth state
    state_data = await music_service.verify_oauth_state(state)
    if not state_data:
        return RedirectResponse(
            url=f"{frontend_url}/services/spotify/error?message=invalid_state",
            status_code=status.HTTP_302_FOUND,
        )

    user_id = state_data["user_id"]

    try:
        # Exchange code for tokens
        tokens = await music_service.spotify.exchange_code(code)

        # Get user profile
        access_token = tokens.get("access_token", "")
        profile = await music_service.spotify.get_current_user(access_token)

        # Store the service connection
        await music_service.create_or_update_spotify_service(user_id, tokens, profile)

        return RedirectResponse(
            url=f"{frontend_url}/services/spotify/success",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        error_msg = quote(str(e)[:100], safe="")  # URL-encode for safety
        return RedirectResponse(
            url=f"{frontend_url}/services/spotify/error?message={error_msg}",
            status_code=status.HTTP_302_FOUND,
        )


# -----------------------------------------------------------------------------
# Last.fm Connection
# -----------------------------------------------------------------------------


@router.post("/lastfm/connect", response_model=ConnectedServiceResponse)
async def connect_lastfm(
    request: LastFmConnectRequest,
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
) -> ConnectedServiceResponse:
    """Connect Last.fm account.

    Last.fm uses API key authentication rather than OAuth,
    so we just need the username. We validate the username
    by fetching the user's profile from Last.fm.
    """
    try:
        service = await music_service.create_lastfm_service(user.id, request.username)

        return ConnectedServiceResponse(
            service_type=service.service_type,
            service_username=service.service_username,
            last_sync_at=service.last_sync_at.isoformat() if service.last_sync_at else None,
            sync_status=service.sync_status,
            sync_error=service.sync_error,
            tracks_synced=service.tracks_synced,
        )

    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


# -----------------------------------------------------------------------------
# Disconnect Service
# -----------------------------------------------------------------------------


@router.delete("/{service_type}", response_model=DisconnectResponse)
async def disconnect_service(
    service_type: str,
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
) -> DisconnectResponse:
    """Disconnect a music service.

    Removes the service connection and any stored tokens.
    Does not delete synced UserSong records.
    """
    if service_type not in ("spotify", "lastfm"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid service type: {service_type}",
        )

    try:
        await music_service.delete_service(user.id, service_type)
        return DisconnectResponse(message=f"Successfully disconnected {service_type}")

    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_type} is not connected",
        )


# -----------------------------------------------------------------------------
# Sync Operations
# -----------------------------------------------------------------------------


@router.post("/sync", response_model=SyncResultResponse)
async def trigger_sync(
    user: CurrentUser,
    sync_service: SyncServiceDep,
) -> SyncResultResponse:
    """Trigger sync for all connected services.

    Fetches listening history from all connected services,
    matches tracks against the karaoke catalog, and creates
    UserSong records.

    This operation may take several seconds depending on the
    size of the user's listening history.
    """
    results = await sync_service.sync_all_services(user.id)

    return SyncResultResponse(
        results=[
            SyncResultItem(
                service_type=r.service_type,
                tracks_fetched=r.tracks_fetched,
                tracks_matched=r.tracks_matched,
                user_songs_created=r.user_songs_created,
                user_songs_updated=r.user_songs_updated,
                error=r.error,
            )
            for r in results
        ]
    )


@router.get("/sync/status", response_model=SyncStatusResponse)
async def get_sync_status(
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
) -> SyncStatusResponse:
    """Get current sync status for all services.

    Returns the sync status, last sync time, and track count
    for each connected service.
    """
    services = await music_service.get_user_services(user.id)

    return SyncStatusResponse(
        services=[
            ConnectedServiceResponse(
                service_type=svc.service_type,
                service_username=svc.service_username,
                last_sync_at=svc.last_sync_at.isoformat() if svc.last_sync_at else None,
                sync_status=svc.sync_status,
                sync_error=svc.sync_error,
                tracks_synced=svc.tracks_synced,
            )
            for svc in services
        ]
    )
