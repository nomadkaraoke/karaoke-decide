"""Music service connection routes.

Handles OAuth flows, service connections, and sync operations.
"""

import asyncio
import html
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, TypeGuard, cast, get_args
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from backend.api.deps import (
    CurrentUser,
    FirestoreServiceDep,
    ImportIngestServiceDep,
    MusicServiceServiceDep,
    OptionalUser,
    Settings,
    SyncServiceDep,
    VerifiedUser,
    get_catalog_lookup_dep,
    get_firestore,
)
from backend.api.routes.import_models import ImportResultResponse
from backend.api.routes.imports_export import router as _imports_export_router
from backend.config import BackendSettings
from backend.i18n import DEFAULT_LOCALE, get_locale_from_request, get_locale_prefix, t
from backend.models.sync_job import SyncJob, SyncJobStatus
from backend.services.catalog_lookup import CatalogLookup
from backend.services.cloud_tasks_service import get_cloud_tasks_service
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import ImportSource
from backend.services.music_service_service import (
    ListenBrainzTokenUnavailableError,
    MusicServiceError,
    MusicServiceService,
)
from backend.services.screenshot_import_service import (
    MAX_CONFIRM_ITEMS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_REQUEST,
    Candidate,
    ConfirmItem,
    DailyLimitError,
    ScreenshotImportService,
    ScreenshotUnavailableError,
    get_screenshot_extractor,
)
from backend.services.sync_service import ImportRateLimitedError
from karaoke_decide.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from karaoke_decide.services.apple_music import AppleMusicNotConfiguredError
from karaoke_decide.services.listenbrainz import (
    LB_IMPORT_TERMINAL_STATUSES,
    ListenBrainzImportInProgressError,
    ListenBrainzRateLimitedError,
)
from karaoke_decide.services.metabrainz_oauth import new_pkce_pair
from karaoke_decide.services.spotify import SpotifyUserNotAllowedError
from karaoke_decide.services.spotify_embed import (
    InvalidSpotifyPlaylistUrlError,
    SpotifyEmbedFetchError,
    SpotifyEmbedFormatChangedError,
    SpotifyPlaylistNotFoundError,
)
from karaoke_decide.services.youtube_music import (
    InvalidPlaylistUrlError,
    PlaylistFetchError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
)

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
    tracks_synced: int  # Karaoke-matched tracks
    songs_synced: int  # Total unique songs synced
    # Added in 3b (decide web ignores them): rows stored for this source, last import, summary status.
    artists: int = 0
    songs: int = 0
    last_import_at: str | None = None
    status: str = "ok"


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


LISTENBRAINZ_UPLOAD_MAX_BYTES = 30 * 1024 * 1024


class Capabilities(BaseModel):
    """Which optional import features this deployment has enabled."""

    screenshot_import: bool
    listenbrainz_oauth: bool
    listenbrainz_upload_max_bytes: int = LISTENBRAINZ_UPLOAD_MAX_BYTES


@router.get("/capabilities", response_model=Capabilities)
async def get_capabilities(settings: Settings) -> Capabilities:
    """Feature flags for the Connect hub (no auth; the app hides cards that are off)."""
    return _capabilities(settings)


# -----------------------------------------------------------------------------
# Screenshot import (Gemini Flash reads the screenshot; the user confirms; ingest saves)
# -----------------------------------------------------------------------------


class ScreenshotExtractResponse(BaseModel):
    candidates: list[Candidate]


class ScreenshotConfirmRequest(BaseModel):
    items: list[ConfirmItem] = Field(max_length=MAX_CONFIRM_ITEMS)


async def get_screenshot_import_service(
    settings: Settings,
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    ingest: ImportIngestServiceDep,
    catalog: Annotated[CatalogLookup, Depends(get_catalog_lookup_dep)],
) -> ScreenshotImportService:
    extractor = get_screenshot_extractor(settings) if settings.screenshot_import_enabled else None
    return ScreenshotImportService(firestore, ingest, catalog, extractor)


ScreenshotImportServiceDep = Annotated[ScreenshotImportService, Depends(get_screenshot_import_service)]


@router.post("/screenshot/extract", response_model=ScreenshotExtractResponse)
async def extract_screenshot(
    user: VerifiedUser, service: ScreenshotImportServiceDep, images: Annotated[list[UploadFile] | None, File()] = None
) -> ScreenshotExtractResponse:
    """Read artists/songs off 1-5 screenshots (each at most 4 MiB, image/*).

    Errors: 400 no_images / too_many_images / not_an_image, 413 image_too_large,
    429 daily_limit (10 images per UTC day), 503 screenshot_unavailable.
    """
    if not images:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_images")
    if len(images) > MAX_IMAGES_PER_REQUEST:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "too_many_images")
    if any(not (f.content_type or "").startswith("image/") for f in images):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_an_image")
    if service.extractor is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "screenshot_unavailable")
    blobs: list[tuple[bytes, str]] = []
    for f in images:
        data = await f.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "image_too_large")
        blobs.append((data, f.content_type or "image/png"))
    try:
        candidates = await service.extract(user.id, blobs)
    except DailyLimitError:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "daily_limit")
    except ScreenshotUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "screenshot_unavailable")
    return ScreenshotExtractResponse(candidates=candidates)


@router.post("/screenshot/confirm", response_model=ImportResultResponse)
async def confirm_screenshot(
    body: ScreenshotConfirmRequest, user: VerifiedUser, service: ScreenshotImportServiceDep
) -> ImportResultResponse:
    """Save the candidates the user kept (source ``screenshot``)."""
    result = await service.confirm(user.id, body.items)
    return ImportResultResponse.from_result("screenshot", result)


class PendingListenBrainzImport(BaseModel):
    id: str
    status: str


class SourceSummary(BaseModel):
    """One source in the app's "Connected sources" list."""

    source: str
    connected: bool
    username: str | None = None
    artists: int = 0
    songs: int = 0
    last_import_at: str | None = None
    status: Literal["ok", "needs_reconnect", "importing", "stale", "error"] = "ok"
    auth: Literal["username", "oauth"] | None = None
    pending_listenbrainz_import: PendingListenBrainzImport | None = None


class SourcesSummaryResponse(BaseModel):
    sources: list[SourceSummary]
    capabilities: Capabilities
    known_songs: int = 0  # distinct songs across all sources (deduped by song_key)


class RemoveDataResponse(BaseModel):
    removed_songs: int
    removed_artists: int


def _capabilities(settings: BackendSettings) -> Capabilities:
    return Capabilities(
        screenshot_import=settings.screenshot_import_enabled,
        listenbrainz_oauth=settings.listenbrainz_oauth_active,
    )


async def _known_songs(music_service: MusicServiceService, user_id: str) -> int:
    """``count_known_songs``, or 0 when it fails (the rest of the summary still answers)."""
    try:
        return await music_service.count_known_songs(user_id)
    except Exception:
        logger.warning("known_songs count failed for %s; summary reports 0", user_id, exc_info=True)
        return 0


@router.get("/summary", response_model=SourcesSummaryResponse)
async def get_sources_summary(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
    settings: Settings,
) -> SourcesSummaryResponse:
    """Every connected or imported source with its counts and status, plus the capability flags."""
    rows, known = await asyncio.gather(
        music_service.get_source_summaries(user.id), _known_songs(music_service, user.id)
    )
    return SourcesSummaryResponse(
        sources=[SourceSummary(**r) for r in rows], capabilities=_capabilities(settings), known_songs=known
    )


@router.delete("/sources/{source}/data", response_model=RemoveDataResponse)
async def remove_source_data(
    source: str,
    user: VerifiedUser,
    ingest: ImportIngestServiceDep,
    music_service: MusicServiceServiceDep,
    request: Request,
) -> RemoveDataResponse:
    """Remove what one source contributed (rows sung or loved, or held by another source, are kept)
    and its connection, if it has one."""
    if source not in get_args(ImportSource):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=t(get_locale_from_request(request), "services.invalidServiceType", service_type=source),
        )
    songs, artists = await ingest.remove_source_data(user.id, cast(ImportSource, source))
    try:
        await music_service.delete_service(user.id, source)
    except NotFoundError:
        pass
    return RemoveDataResponse(removed_songs=songs, removed_artists=artists)


@router.get("", response_model=list[ConnectedServiceResponse])
async def list_services(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
) -> list[ConnectedServiceResponse]:
    """List user's connected music services.

    Returns information about each connected service including
    sync status and track counts.
    """
    extra: dict[str, dict[str, Any]] = {}
    try:
        services, rows = await music_service.get_services_with_summaries(user.id)
        extra = {r["source"]: r for r in rows}
    except Exception:
        # The counts are extras; decide web needs this route, so fall back to zeros / None.
        logger.warning("per-source summary lookup failed for GET /api/services; returning zeros", exc_info=True)
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
            artists=extra.get(svc.service_type, {}).get("artists", 0),
            songs=extra.get(svc.service_type, {}).get("songs", 0),
            last_import_at=extra.get(svc.service_type, {}).get("last_import_at"),
            status=extra.get(svc.service_type, {}).get("status", "ok"),
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


# States are ``secrets.token_urlsafe`` strings; anything else is forged or mangled and never
# reaches Firestore (a document id with "/" or of odd length would 500 there).
_OAUTH_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _well_formed_state(state: str | None) -> TypeGuard[str]:
    return state is not None and _OAUTH_STATE_RE.fullmatch(state) is not None


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
        reason = "denied" if error == "access_denied" else "spotify_error"
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message={reason}",
            status_code=status.HTTP_302_FOUND,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message=missing_params",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify OAuth state
    state_data = await music_service.verify_oauth_state(state) if _well_formed_state(state) else None
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

    except SpotifyUserNotAllowedError:
        # Expected while our Spotify app is in Development Mode: one line, no traceback.
        logger.warning("Spotify callback: user %s is not on the Spotify app allowlist", user_id)
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message=not_allowlisted",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception:
        logger.warning("Spotify callback exchange failed", exc_info=True)
        return RedirectResponse(
            url=f"{frontend_url}{locale_prefix}/services/spotify/error?message=exchange_failed",
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
# ListenBrainz via MetaBrainz OAuth (authorization code + PKCE)
# -----------------------------------------------------------------------------

ListenBrainzReturnTo = Literal["app", "web"]
ListenBrainzOAuthReason = Literal["denied", "missing_params", "invalid_state", "exchange_failed", "validate_failed"]

_APP_RETURN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nomad Karaoke</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px 16px">
<p><a href="{url}">Return to the app</a></p></body></html>"""


class ListenBrainzOAuthStartResponse(BaseModel):
    auth_url: str


@router.get("/listenbrainz/oauth/start", response_model=ListenBrainzOAuthStartResponse)
async def listenbrainz_oauth_start(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
    settings: Settings,
    return_to: ListenBrainzReturnTo = Query(...),
) -> ListenBrainzOAuthStartResponse:
    """Start connecting ListenBrainz through MetaBrainz OAuth (404 ``oauth_disabled`` when off)."""
    if not settings.listenbrainz_oauth_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="oauth_disabled")
    code_verifier, code_challenge = new_pkce_pair()
    state = await music_service.create_oauth_state(
        user.id, "listenbrainz", extra={"return_to": return_to, "code_verifier": code_verifier}
    )
    return ListenBrainzOAuthStartResponse(auth_url=music_service.metabrainz.authorize_url(state, code_challenge))


def _listenbrainz_oauth_return(
    settings: BackendSettings, return_to: str, reason: ListenBrainzOAuthReason | None = None
) -> HTMLResponse | RedirectResponse:
    """302 to the app scheme (with a tiny HTML fallback link) or to the web Music I Know page."""
    headers = {"Cache-Control": "no-store"}
    if return_to == "app":
        query = "status=connected" if reason is None else f"status=error&reason={reason}"
        url = f"{settings.mobile_app_scheme}://services/listenbrainz?{query}"
        return HTMLResponse(
            _APP_RETURN_PAGE.format(url=html.escape(url, quote=True)),
            status_code=status.HTTP_302_FOUND,
            headers={"Location": url, **headers},
        )
    query = "listenbrainz=connected" if reason is None else f"listenbrainz=error&reason={reason}"
    url = f"{settings.frontend_url}{get_locale_prefix(DEFAULT_LOCALE)}/music-i-know?{query}"
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND, headers=headers)


_ZIP_MAGIC = b"PK\x03\x04"


LB_PENDING_IMPORT_MAX_AGE = timedelta(hours=6)


def _pending_import_blocks(pending: object) -> bool:
    """Whether a stored pending import is recent and not known to be finished."""
    if not isinstance(pending, dict) or not pending.get("id") or pending.get("status") in LB_IMPORT_TERMINAL_STATUSES:
        return False
    try:
        return datetime.now(UTC) - datetime.fromisoformat(pending["submitted_at"]) < LB_PENDING_IMPORT_MAX_AGE
    except (KeyError, TypeError, ValueError):
        return True


class ListenBrainzUploadResponse(BaseModel):
    import_id: str
    status: str


@router.post("/listenbrainz/upload-export", response_model=ListenBrainzUploadResponse)
async def upload_listenbrainz_export(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
    file: Annotated[UploadFile, File()],
) -> ListenBrainzUploadResponse:
    """Hand the user's Spotify export zip (at most 30 MiB) to ListenBrainz's importer for them.

    Needs a ListenBrainz connection made with OAuth. The listens are not ingested here (the app
    already sent its aggregates); this only feeds ListenBrainz. Errors: 409 not_oauth_connected /
    needs_reconnect / import_in_progress, 413 too_large, 400 not_a_zip, 429 rate_limited
    (Retry-After), 503 listenbrainz_unavailable.
    """
    doc = await music_service.get_service_doc(user.id, "listenbrainz")
    if doc is None or doc.get("auth") != "oauth":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_oauth_connected")
    service = music_service.service_from_doc(doc)

    data = await file.read(LISTENBRAINZ_UPLOAD_MAX_BYTES + 1)
    if len(data) > LISTENBRAINZ_UPLOAD_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large")
    if not data.startswith(_ZIP_MAGIC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_a_zip")

    try:
        token = await music_service.get_listenbrainz_token(service)
    except ListenBrainzTokenUnavailableError as e:
        logger.warning("ListenBrainz token unavailable for %s: %s", user.id, e)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "listenbrainz_unavailable", headers={"Retry-After": "30"}
        )
    except MusicServiceError:
        raise HTTPException(status.HTTP_409_CONFLICT, "needs_reconnect")

    lb = music_service.listenbrainz
    pending = doc.get("lb_pending_import")
    try:
        if _pending_import_blocks(pending):
            try:
                latest = (await lb.get_import_status(token, str(pending["id"]))).get("status")  # type: ignore[index]
            except ExternalServiceError as e:
                # Can't tell (404/401/429...): never block on it. LB's own "already in progress" 400 is the guard.
                logger.warning("Could not check ListenBrainz import status for %s: %s", user.id, e)
                latest = None
            if latest and latest not in LB_IMPORT_TERMINAL_STATUSES:
                raise HTTPException(status.HTTP_409_CONFLICT, "import_in_progress")
        filename = file.filename if file.filename and file.filename.lower().endswith(".zip") else "spotify-export.zip"
        import_id = await lb.submit_import(token, data, filename)
    except ListenBrainzImportInProgressError:
        raise HTTPException(status.HTTP_409_CONFLICT, "import_in_progress")
    except ListenBrainzRateLimitedError as e:
        logger.warning("ListenBrainz rate limited the export upload for %s (retry in %ss)", user.id, e.retry_after)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            headers={"Retry-After": str(max(1, round(e.retry_after)))},
        )
    except ExternalServiceError as e:
        logger.warning("ListenBrainz export upload failed for %s: %s", user.id, e)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "listenbrainz_unavailable")

    await music_service.set_pending_listenbrainz_import(user.id, import_id)
    return ListenBrainzUploadResponse(import_id=import_id, status="waiting")


@router.get("/listenbrainz/oauth/callback", response_model=None)
async def listenbrainz_oauth_callback(
    music_service: MusicServiceServiceDep,
    settings: Settings,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> HTMLResponse | RedirectResponse:
    """MetaBrainz redirects here (no auth; the state identifies the user and the return target).

    Errors redirect with a fixed reason code, never exception text. An unknown state can't
    say where the user came from, so it returns to the web page.
    """
    state_data = await music_service.verify_oauth_state(state) if _well_formed_state(state) else None
    if state_data is not None and state_data.get("service_type") != "listenbrainz":
        state_data = None
    return_to = state_data.get("return_to", "web") if state_data else "web"

    def fail(reason: ListenBrainzOAuthReason) -> HTMLResponse | RedirectResponse:
        return _listenbrainz_oauth_return(settings, return_to, reason)

    if error:
        return fail("denied")
    if not code or not state:
        return fail("missing_params")
    if state_data is None or not state_data.get("code_verifier"):
        return fail("invalid_state")
    if not settings.listenbrainz_oauth_active:  # switched off mid-flow: never store unencrypted tokens
        return fail("exchange_failed")

    try:
        tokens = await music_service.metabrainz.exchange_code(code, state_data["code_verifier"])
    except ExternalServiceError as e:
        logger.warning("ListenBrainz OAuth code exchange failed: %s", e)
        return fail("exchange_failed")

    try:
        # LB's /1/validate-token doesn't know MetaBrainz OAuth tokens, so the username comes
        # from MetaBrainz userinfo; the listen-count lookup confirms a ListenBrainz account exists.
        username = await music_service.metabrainz.userinfo(str(tokens["access_token"]))
        await music_service.listenbrainz.get_user_listen_count(username)
    except ExternalServiceError as e:
        logger.warning("ListenBrainz OAuth account check failed: %s", e)
        return fail("validate_failed")

    try:
        await music_service.create_listenbrainz_oauth_service(state_data["user_id"], tokens, username)
    except Exception:
        logger.exception("Storing the ListenBrainz OAuth connection failed")
        return fail("exchange_failed")
    return _listenbrainz_oauth_return(settings, return_to)


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
# Spotify public playlist import (embed page, best-effort)
# -----------------------------------------------------------------------------


class SpotifyPlaylistImportRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=500, description="Playlist link, URI, id or share text")


class SpotifyPlaylistImportResponse(BaseModel):
    playlist_title: str
    tracks_fetched: int
    tracks_matched: int


@router.post("/spotify-playlist/import", response_model=SpotifyPlaylistImportResponse)
async def import_spotify_playlist(
    request_body: SpotifyPlaylistImportRequest,
    user: VerifiedUser,
    sync_service: SyncServiceDep,
) -> SpotifyPlaylistImportResponse:
    """Import up to 100 tracks from a public Spotify playlist link (no Spotify login).

    - 400: not a Spotify playlist link
    - 404: playlist doesn't exist or is private
    - 422: Spotify changed its embed page and we can't read it (best-effort feature)
    - 429: too many imports (30 s cooldown / 20 per day)
    - 503: Spotify couldn't be reached (503, not 502: Cloudflare replaces origin 502 bodies)
    """
    try:
        result = await sync_service.import_spotify_playlist(user.id, request_body.url)
    except ImportRateLimitedError:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many playlist imports")
    except InvalidSpotifyPlaylistUrlError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a Spotify playlist link")
    except SpotifyPlaylistNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playlist not found or private")
    except SpotifyEmbedFormatChangedError as e:
        logger.warning(f"Spotify embed format changed: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read this Spotify playlist right now"
        )
    except SpotifyEmbedFetchError as e:
        logger.warning(f"Spotify embed fetch failed: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not reach Spotify")

    return SpotifyPlaylistImportResponse(
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


@router.get("/apple-music/app-developer-token", response_model=AppleMusicDeveloperTokenResponse)
async def apple_music_app_developer_token(
    user: VerifiedUser,
    music_service: MusicServiceServiceDep,
) -> AppleMusicDeveloperTokenResponse:
    """Developer token for the iOS app's native MusicKit sign-in (no origin claim: native requests send no
    Origin header). Signed-in users only, since an unrestricted token works from anywhere."""
    try:
        token, expires_at = music_service.apple_music_signer.token()
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


async def _find_resumable_job(user_id: str, firestore: FirestoreService) -> tuple[str, bool] | None:
    """The user's latest sync job as (id, stalled) if it is still live (pending/in progress), else None.

    stalled=True means a dead heartbeat or lost task (needs a re-enqueue); False means it is healthy.
    """
    docs = await firestore.query_documents(
        collection="sync_jobs",
        filters=[("user_id", "==", user_id)],
        order_by="created_at",
        order_direction="DESCENDING",
        limit=1,
    )
    for data in docs:
        if not data:
            continue
        job = SyncJob.from_dict(data)
        if job.status in (SyncJobStatus.PENDING, SyncJobStatus.IN_PROGRESS):
            return str(data["id"]), job.is_stalled()
    return None


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
    locale = get_locale_from_request(request)
    try:
        live = await _find_resumable_job(user.id, firestore)
        if live and not live[1]:
            # A healthy job is already running or queued: hand back its id, no sibling job or task.
            return SyncJobStartResponse(
                job_id=live[0],
                status="in_progress",
                message=t(locale, "services.syncStarted"),
            )
        if live:
            stalled_id = live[0]
            # Resume the stalled job instead of starting a second one next to it.
            try:
                get_cloud_tasks_service(settings).create_sync_task(job_id=stalled_id, user_id=user.id)
            except Exception as e:
                raise SyncEnqueueError(str(e)) from e
            return SyncJobStartResponse(
                job_id=stalled_id,
                status="stalled",
                message=t(locale, "services.syncStarted"),
            )
        job_id = await _start_sync_job(user.id, settings, firestore)
    except SyncEnqueueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=t(locale, "services.failedToStartSync", error=str(e)),
        )

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

                # A running job whose heartbeat went quiet is stalled, not failed: the retried
                # Cloud Task (or the next POST /sync) resumes it from its checkpoint.
                status_value = job.status.value
                if job.is_stalled():
                    status_value = "stalled"

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
                        status=status_value,
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


router.include_router(_imports_export_router)
