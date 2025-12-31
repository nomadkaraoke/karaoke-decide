"""Music service connection routes.

Handles OAuth flows, service connections, and sync operations.
"""

import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend.api.deps import (
    CurrentUser,
    FirestoreServiceDep,
    MusicServiceServiceDep,
    Settings,
)
from backend.models.sync_job import SyncJob, SyncJobStatus
from backend.services.cloud_tasks_service import get_cloud_tasks_service
from karaoke_decide.core.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

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
    artists_stored: int = 0
    error: str | None


class SyncResultResponse(BaseModel):
    """Response from sync operation (legacy synchronous)."""

    results: list[SyncResultItem]


class SyncJobStartResponse(BaseModel):
    """Response when async sync job is started."""

    job_id: str
    status: str
    message: str


class SyncJobProgressResponse(BaseModel):
    """Progress information for a sync job."""

    current_service: str | None
    current_phase: str | None
    total_tracks: int
    processed_tracks: int
    matched_tracks: int
    percentage: int


class SyncJobStatusResponse(BaseModel):
    """Status of a sync job."""

    job_id: str
    status: str
    progress: SyncJobProgressResponse | None
    results: list[SyncResultItem] | None
    error: str | None
    created_at: str
    completed_at: str | None


class SyncStatusResponse(BaseModel):
    """Current sync status across all services."""

    services: list[ConnectedServiceResponse]
    active_job: SyncJobStatusResponse | None = None


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


@router.post("/sync", response_model=SyncJobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    user: CurrentUser,
    settings: Settings,
    firestore: FirestoreServiceDep,
) -> SyncJobStartResponse:
    """Trigger async sync for all connected services.

    Creates a background job that fetches listening history from all
    connected services, matches tracks against the karaoke catalog,
    and creates UserSong records.

    Returns immediately with a job_id. Use GET /sync/status to poll
    for progress. An email will be sent when sync completes.
    """
    # Create sync job
    job_id = str(uuid.uuid4())
    job = SyncJob(
        id=job_id,
        user_id=user.id,
        status=SyncJobStatus.PENDING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # Store job in Firestore
    await firestore.set_document("sync_jobs", job_id, job.to_dict())

    # Enqueue Cloud Task
    try:
        cloud_tasks = get_cloud_tasks_service(settings)
        cloud_tasks.create_sync_task(job_id=job_id, user_id=user.id)
    except Exception as e:
        # If task creation fails, mark job as failed
        job.status = SyncJobStatus.FAILED
        job.error = f"Failed to enqueue task: {e}"
        await firestore.set_document("sync_jobs", job_id, job.to_dict())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start sync: {e}",
        )

    return SyncJobStartResponse(
        job_id=job_id,
        status="pending",
        message="Sync job started. Poll /sync/status for progress.",
    )


@router.get("/sync/status", response_model=SyncStatusResponse)
async def get_sync_status(
    user: CurrentUser,
    music_service: MusicServiceServiceDep,
    firestore: FirestoreServiceDep,
) -> SyncStatusResponse:
    """Get current sync status for all services.

    Returns the sync status, last sync time, and track count
    for each connected service. Also includes active job progress
    if a sync is currently running.
    """
    services = await music_service.get_user_services(user.id)

    # Find most recent active or recent job for this user
    active_job: SyncJobStatusResponse | None = None
    try:
        # Use the proper async query method
        job_docs = await firestore.query_documents(
            collection="sync_jobs",
            filters=[("user_id", "==", user.id)],
            order_by="created_at",
            order_direction="DESCENDING",
            limit=1,
        )

        for job_data in job_docs:
            if job_data:
                job = SyncJob.from_dict(job_data)

                # Include if pending, in_progress, or completed within last minute
                include_job = job.status in (SyncJobStatus.PENDING, SyncJobStatus.IN_PROGRESS)
                if job.status == SyncJobStatus.COMPLETED and job.completed_at:
                    seconds_since = (datetime.now(UTC) - job.completed_at).total_seconds()
                    include_job = seconds_since < 60
                if include_job:
                    progress = None
                    if job.progress:
                        progress = SyncJobProgressResponse(
                            current_service=job.progress.current_service,
                            current_phase=job.progress.current_phase,
                            total_tracks=job.progress.total_tracks,
                            processed_tracks=job.progress.processed_tracks,
                            matched_tracks=job.progress.matched_tracks,
                            percentage=job.progress.percentage,
                        )

                    results = None
                    if job.results:
                        results = [
                            SyncResultItem(
                                service_type=r.service_type,
                                tracks_fetched=r.tracks_fetched,
                                tracks_matched=r.tracks_matched,
                                user_songs_created=r.user_songs_created,
                                user_songs_updated=r.user_songs_updated,
                                artists_stored=r.artists_stored,
                                error=r.error,
                            )
                            for r in job.results
                        ]

                    active_job = SyncJobStatusResponse(
                        job_id=job.id,
                        status=job.status.value,
                        progress=progress,
                        results=results,
                        error=job.error,
                        created_at=job.created_at.isoformat(),
                        completed_at=job.completed_at.isoformat() if job.completed_at else None,
                    )
    except Exception as e:
        # Log the actual error - likely missing Firestore composite index
        logger.error(f"Failed to query sync_jobs for user {user.id}: {e}")
        # Continue without active job - frontend will show sync button

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
        ],
        active_job=active_job,
    )
