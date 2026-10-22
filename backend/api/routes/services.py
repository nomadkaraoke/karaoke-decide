"""Music service connection routes.

Handles OAuth flows, service connections, and sync operations.
"""

import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from backend.api.deps import (
    CurrentUser,
    FirestoreServiceDep,
    MusicServiceServiceDep,
    OptionalUser,
    Settings,
    SyncServiceDep,
    VerifiedUser,
)
from backend.config import BackendSettings
from backend.i18n import DEFAULT_LOCALE, get_locale_from_request, get_locale_prefix, t
from backend.models.sync_job import SyncJob, SyncJobStatus
from backend.services.cloud_tasks_service import get_cloud_tasks_service
from backend.services.firestore_service import FirestoreService
from backend.services.sync_service import ImportRateLimitedError
from karaoke_decide.core.exceptions import NotFoundError, ValidationError
from karaoke_decide.services.apple_music import AppleMusicNotConfiguredError
from karaoke_decide.services.youtube_music import (
    InvalidPlaylistUrlError,
    PlaylistFetchError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
)

logger = logging.getLogger(__name__)

# Sync job is considered stale if no progress update in this many seconds
# BigQuery matching can take 3+ minutes for large track sets, so 5 minutes is reasonable
STALE_JOB_THRESHOLD_SECONDS = 300

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
    tracks_synced: int  # Karaoke-matched tracks
    songs_synced: int  # Total unique songs synced


class SpotifyConnectResponse(BaseModel):
    """Response for starting Spotify OAuth."""

    auth_url: str


class LastFmConnectRequest(BaseModel):
    """Request to connect Last.fm account."""

    username: str


class ListenBrainzConnectRequest(BaseModel):
    """Request to connect ListenBrainz account."""

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
    user: VerifiedUser,
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
            songs_synced=svc.songs_synced,
        )
        for svc in services
    ]


# -----------------------------------------------------------------------------
# Spotify OAuth Flow
# -----------------------------------------------------------------------------


@router.post("/spotify/connect", response_model=SpotifyConnectResponse)
async def connect_spotify(
    user: VerifiedUser,
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
    # OAuth callbacks come from Spotify, not the user — no Accept-Language header.
    # Use the default locale for redirect URLs.
    locale_prefix = get_locale_prefix(DEFAULT_LOCALE)

    # Handle OAuth errors
    if error:
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message={quote(error, safe='')}",
            status_code=status.HTTP_302_FOUND,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message=missing_params",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify OAuth state
    state_data = await music_service.verify_oauth_state(state)
    if not state_data:
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message=invalid_state",
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
            url=f"{frontend_url}{locale_prefix}/services/spotify/success",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        error_msg = quote(str(e)[:100], safe="")  # URL-encode for safety
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message={error_msg}",
            status_code=status.HTTP_302_FOUND,
        )


# -----------------------------------------------------------------------------
# Last.fm Connection
# -----------------------------------------------------------------------------


@router.post("/lastfm/connect", response_model=ConnectedServiceResponse)
async def connect_lastfm(
    request_body: LastFmConnectRequest,
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
) -> ConnectedServiceResponse:
    """Connect Last.fm account.

    Last.fm uses API key authentication rather than OAuth,
    so we just need the username. We validate the username
    by fetching the user's profile from Last.fm.
    """
    try:
        service = await music_service.create_lastfm_service(user.id, request_body.username)

        return ConnectedServiceResponse(
            service_type=service.service_type,
            service_username=service.service_username,
            last_sync_at=service.last_sync_at.isoformat() if service.last_sync_at else None,
            sync_status=service.sync_status,
            sync_error=service.sync_error,
            tracks_synced=service.tracks_synced,
            songs_synced=service.songs_synced,
        )

    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


# -----------------------------------------------------------------------------
# ListenBrainz Connection
# -----------------------------------------------------------------------------


@router.post("/listenbrainz/connect", response_model=ConnectedServiceResponse)
async def connect_listenbrainz(
    request_body: ListenBrainzConnectRequest,
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
) -> ConnectedServiceResponse:
    """Connect ListenBrainz account.

    ListenBrainz is an open-source music tracking service that can import
    data from Spotify, Apple Music, and other services. We just need the
    username. We validate the username by fetching the user's listen count.
    """
    try:
        service = await music_service.create_listenbrainz_service(user.id, request_body.username)

        return ConnectedServiceResponse(
            service_type=service.service_type,
            service_username=service.service_username,
            last_sync_at=service.last_sync_at.isoformat() if service.last_sync_at else None,
            sync_status=service.sync_status,
            sync_error=service.sync_error,
            tracks_synced=service.tracks_synced,
            songs_synced=service.songs_synced,
        )

    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


# -----------------------------------------------------------------------------
# YouTube Music Playlist Import
# -----------------------------------------------------------------------------


class YouTubeMusicImportRequest(BaseModel):
    """Request to import a shared YouTube Music playlist."""

    playlist_url: str = Field(..., min_length=1, max_length=500, description="Playlist link or ID")


class YouTubeMusicImportResponse(BaseModel):
    """Result of a YouTube Music playlist import."""

    playlist_title: str
    tracks_fetched: int
    tracks_matched: int  # Tracks with a karaoke version in the catalog


@router.post("/youtube-music/import", response_model=YouTubeMusicImportResponse)
async def import_youtube_music_playlist(
    request_body: YouTubeMusicImportRequest,
    user: CurrentUser,
    sync_service: SyncServiceDep,
) -> YouTubeMusicImportResponse:
    """Import songs from a public or unlisted YouTube Music playlist.

    No YouTube login needed - the user shares a playlist link (e.g. a copy of
    their Liked Music). Guests may import during the quiz; their songs move to
    their account on email verification. Error status codes are stable so the
    frontend can show localized guidance:

    - 400: not a YouTube playlist link
    - 422: the always-private Liked Music / Liked videos playlist was shared
    - 404: playlist doesn't exist or is private
    - 429: too many imports (per-user cooldown / daily cap)
    - 503: YouTube couldn't be reached (503, not 502: Cloudflare replaces origin 502 bodies)
    """
    try:
        result = await sync_service.import_youtube_music_playlist(
            user.id, request_body.playlist_url, is_guest=user.is_guest
        )
    except ImportRateLimitedError:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many playlist imports")
    except InvalidPlaylistUrlError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a YouTube playlist link")
    except PrivatePlaylistError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Liked Music is always private - copy it into a public or unlisted playlist",
        )
    except PlaylistNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found or private")
    except PlaylistFetchError as e:
        logger.warning(f"YouTube Music playlist fetch failed: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not reach YouTube Music")

    return YouTubeMusicImportResponse(
        playlist_title=result["playlist_title"],
        tracks_fetched=result["tracks_fetched"],
        tracks_matched=result["tracks_matched"],
    )


# -----------------------------------------------------------------------------
# Apple Music (MusicKit)
# -----------------------------------------------------------------------------


class AppleMusicDeveloperTokenResponse(BaseModel):
    """Developer token for MusicKit JS on decide's web origin."""

    developer_token: str
    expires_at: int  # Unix seconds


class AppleMusicLinkResponse(BaseModel):
    """A one-time link that connects Apple Music from a browser the user isn't signed in to (the app)."""

    connect_url: str
    expires_in: int  # Seconds


class AppleMusicConnectRequest(BaseModel):
    """Music User Token from MusicKit JS; `state` comes from /apple-music/link when not signed in."""

    music_user_token: str = Field(..., min_length=1, max_length=4096)
    state: str | None = Field(default=None, max_length=128)


class AppleMusicConnectResponse(BaseModel):
    """The connected service, plus the sync job that started reading the library."""

    service: ConnectedServiceResponse
    sync_job_id: str | None


@router.get("/apple-music/developer-token", response_model=AppleMusicDeveloperTokenResponse)
async def apple_music_developer_token(
    music_service: MusicServiceServiceDep,
    settings: Settings,
) -> AppleMusicDeveloperTokenResponse:
    """Developer token for MusicKit JS. Public: it only works from decide's own web origin."""
    try:
        token, expires_at = music_service.apple_music_signer.token(origins=(settings.frontend_url.rstrip("/"),))
    except AppleMusicNotConfiguredError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Apple Music is not available")
    return AppleMusicDeveloperTokenResponse(developer_token=token, expires_at=expires_at)


@router.post("/apple-music/link", response_model=AppleMusicLinkResponse)
async def apple_music_link(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
    settings: Settings,
    request: Request,
) -> AppleMusicLinkResponse:
    """Start connecting Apple Music from the app.

    The app opens `connect_url` (decide web) in an in-app browser. That page signs the user in
    to Apple Music with MusicKit JS and posts the token back with this link's one-time state,
    then returns to the app at `nomadkaraoke://music-connected`. The state expires in 10 minutes.
    """
    state = await music_service.create_oauth_state(user.id, "apple_music", account_hint=mask_email(user.email))
    prefix = get_locale_prefix(get_locale_from_request(request))
    url = f"{settings.frontend_url.rstrip('/')}{prefix}/connect/apple-music?state={quote(state)}&return=app"
    return AppleMusicLinkResponse(connect_url=url, expires_in=music_service.STATE_EXPIRATION_MINUTES * 60)


class AppleMusicLinkInfoResponse(BaseModel):
    """Which Nomad Karaoke account a hand-off link connects Apple Music to (masked email)."""

    account: str | None


def mask_email(email: str | None) -> str | None:
    """`andrew@example.com` -> `a•••@example.com`: enough to recognise your own account, not to harvest it."""
    if not email or "@" not in email:
        return None
    name, domain = email.split("@", 1)
    return f"{name[:1]}•••@{domain}"


@router.get("/apple-music/link-info", response_model=AppleMusicLinkInfoResponse)
async def apple_music_link_info(
    music_service: MusicServiceServiceDep,
    state: str = Query(..., max_length=128),
) -> AppleMusicLinkInfoResponse:
    """The account a hand-off link belongs to, shown before connecting so nobody connects their
    Apple Music to someone else's account from a forwarded link. 404 when expired or unknown."""
    link = await music_service.peek_oauth_state(state)
    if link is None or link["service_type"] != "apple_music":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link expired - start again")
    return AppleMusicLinkInfoResponse(account=link.get("account_hint"))


@router.post("/apple-music/connect", response_model=AppleMusicConnectResponse)
async def connect_apple_music(
    request_body: AppleMusicConnectRequest,
    user: OptionalUser,
    music_service: MusicServiceServiceDep,
    settings: Settings,
    firestore: FirestoreServiceDep,
) -> AppleMusicConnectResponse:
    """Connect Apple Music with a Music User Token and start syncing it.

    Signed-in callers (decide web) send their own token. The app's browser hand-off sends the
    `state` from /apple-music/link instead; it's consumed once Apple accepts the token.

    - 401: neither a signed-in user nor a valid, unexpired state
    - 422: Apple refused the Music User Token
    - 503: Apple Music isn't configured on this server
    """
    if request_body.state:
        # Peek first and consume only once Apple has accepted the token, so a refused or failed
        # attempt can be retried from the same page.
        link = await music_service.peek_oauth_state(request_body.state)
        if link is None or link["service_type"] != "apple_music" or not link["user_id"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Link expired - start again")
        user_id = link["user_id"]
    elif user is not None and not user.is_guest:
        user_id = user.id
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to connect Apple Music")

    try:
        service = await music_service.create_apple_music_service(user_id, request_body.music_user_token)
    except AppleMusicNotConfiguredError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Apple Music is not available")
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    if request_body.state:
        await music_service.verify_oauth_state(request_body.state)  # one connection per link

    try:
        job_id: str | None = await _start_sync_job(user_id, settings, firestore)
    except SyncEnqueueError as e:
        # Connected all the same; the next sync (button or daily) reads the library.
        logger.warning(f"Apple Music connected for {user_id} but the sync didn't start: {e}")
        job_id = None

    return AppleMusicConnectResponse(
        service=ConnectedServiceResponse(
            service_type=service.service_type,
            service_username=service.service_username,
            last_sync_at=service.last_sync_at.isoformat() if service.last_sync_at else None,
            sync_status=service.sync_status,
            sync_error=service.sync_error,
            tracks_synced=service.tracks_synced,
            songs_synced=service.songs_synced,
        ),
        sync_job_id=job_id,
    )


# -----------------------------------------------------------------------------
# Disconnect Service
# -----------------------------------------------------------------------------


@router.delete("/{service_type}", response_model=DisconnectResponse)
async def disconnect_service(
    service_type: str,
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
    request: Request,
) -> DisconnectResponse:
    """Disconnect a music service.

    Removes the service connection and any stored tokens.
    Does not delete synced UserSong records.
    """
    locale = get_locale_from_request(request)

    if service_type not in ("spotify", "lastfm", "listenbrainz", "apple_music"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t(locale, "services.invalidServiceType", service_type=service_type),
        )

    try:
        await music_service.delete_service(user.id, service_type)
        return DisconnectResponse(message=t(locale, "services.disconnected", service_type=service_type))

    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t(locale, "services.serviceNotConnected", service_type=service_type),
        )


# -----------------------------------------------------------------------------
# Sync Operations
# -----------------------------------------------------------------------------


class SyncEnqueueError(Exception):
    """The sync job's Cloud Task couldn't be created (the job is marked failed)."""


async def _start_sync_job(user_id: str, settings: BackendSettings, firestore: FirestoreService) -> str:
    """Create a sync job for all of the user's services and enqueue its Cloud Task. Returns the job id."""
    job_id = str(uuid.uuid4())
    job = SyncJob(
        id=job_id,
        user_id=user_id,
        status=SyncJobStatus.PENDING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await firestore.set_document("sync_jobs", job_id, job.to_dict())

    try:
        cloud_tasks = get_cloud_tasks_service(settings)
        cloud_tasks.create_sync_task(job_id=job_id, user_id=user_id)
    except Exception as e:
        job.status = SyncJobStatus.FAILED
        job.error = f"Failed to enqueue task: {e}"
        await firestore.set_document("sync_jobs", job_id, job.to_dict())
        raise SyncEnqueueError(str(e)) from e
    return job_id


@router.post("/sync", response_model=SyncJobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    user: VerifiedUser,
    settings: Settings,
    firestore: FirestoreServiceDep,
    request: Request,
) -> SyncJobStartResponse:
    """Trigger async sync for all connected services.

    Creates a background job that fetches listening history from all
    connected services, matches tracks against the karaoke catalog,
    and creates UserSong records.

    Returns immediately with a job_id. Use GET /sync/status to poll
    for progress. An email will be sent when sync completes.
    """
    try:
        job_id = await _start_sync_job(user.id, settings, firestore)
    except SyncEnqueueError as e:
        locale = get_locale_from_request(request)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=t(locale, "services.failedToStartSync", error=str(e)),
        )

    locale = get_locale_from_request(request)
    return SyncJobStartResponse(
        job_id=job_id,
        status="pending",
        message=t(locale, "services.syncStarted"),
    )


@router.get("/sync/status", response_model=SyncStatusResponse)
async def get_sync_status(
    user: VerifiedUser,
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

                # Check if in_progress job is stale (no heartbeat for too long)
                if job.status == SyncJobStatus.IN_PROGRESS and job.updated_at:
                    age_seconds = (datetime.now(UTC) - job.updated_at).total_seconds()
                    if age_seconds > STALE_JOB_THRESHOLD_SECONDS:
                        # Auto-mark as failed - job likely crashed
                        logger.warning(
                            f"Marking stale sync job {job.id} as failed "
                            f"(no progress for {int(age_seconds)} seconds)"
                        )
                        timeout_error = f"Sync timed out (no progress for {int(age_seconds)} seconds)"
                        completed_at = datetime.now(UTC)
                        await firestore.update_document(
                            "sync_jobs",
                            job.id,
                            {
                                "status": SyncJobStatus.FAILED.value,
                                "error": timeout_error,
                                "completed_at": completed_at.isoformat(),
                            },
                        )
                        job.status = SyncJobStatus.FAILED
                        job.error = timeout_error
                        job.completed_at = completed_at

                # Include if pending, in_progress, or completed/failed within last minute
                include_job = job.status in (SyncJobStatus.PENDING, SyncJobStatus.IN_PROGRESS)
                if job.status in (SyncJobStatus.COMPLETED, SyncJobStatus.FAILED) and job.completed_at:
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
                songs_synced=svc.songs_synced,
            )
            for svc in services
        ],
        active_job=active_job,
    )
