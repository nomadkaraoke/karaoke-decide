"""Voice session routes: ``/api/voice/*`` (mounted at ``/voice``). Signed-in users.

Behind a flag: a session needs ``settings.voice_session_open`` or an admin user, else
403 ``voice_session_disabled``. Sessions are user-scoped (another user's id is 404).
The personal pool (``/personal-pool``, ``voice_pool_service``) is behind the same flag.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import Message

from backend.api.deps import (
    CurrentUser,
    FirestoreServiceDep,
    Settings,
    VoiceDataStoreDep,
    VoicePoolServiceDep,
    VoiceSessionServiceDep,
)
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.models.voice_personal_pool import PersonalBatchCreated, PersonalPoolOut
from backend.models.voice_session import (
    AttemptIn,
    AttemptOut,
    AttemptPatchIn,
    AttemptPatchOut,
    NextDoneOut,
    NextSongOut,
    ResultOut,
    SessionCreated,
    SessionOut,
    SessionPatch,
    SessionSetup,
    SkipIn,
    VoiceFlagsOut,
    VoiceHistoryOut,
)
from backend.services import voice_recording_service as vrs
from backend.services.catalog_store import CatalogUnavailable
from backend.services.voice_data_store import VoiceDataUnavailable
from backend.services.voice_pool_service import PersonalPoolError
from backend.services.voice_profile_service import VoiceProfileOut
from backend.services.voice_recording_service import RecordingError, VoiceRecordingService
from backend.services.voice_session_service import VoiceSessionError, active_session_id
from karaoke_decide.core.models import User
from karaoke_decide.voice_session.stopping import MAX_ATTEMPTS

router = APIRouter()

_T = TypeVar("_T")


def _enabled(settings: Settings, user: User) -> bool:
    return bool(settings.voice_session_open or user.is_admin)


async def get_session_user(user: CurrentUser, settings: Settings) -> User:
    if not _enabled(settings, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="voice_session_disabled")
    return user


SessionUser = Annotated[User, Depends(get_session_user)]


async def _call(work: Awaitable[_T]) -> _T:
    try:
        return await work
    except (VoiceDataUnavailable, CatalogUnavailable) as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="warming_up") from e
    except VoiceSessionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except PersonalPoolError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail()) from e


@router.get("/flags", response_model=VoiceFlagsOut)
async def flags(
    user: CurrentUser, settings: Settings, store: VoiceDataStoreDep, firestore: FirestoreServiceDep
) -> VoiceFlagsOut:
    return VoiceFlagsOut(
        session_enabled=_enabled(settings, user),
        is_admin=user.is_admin,
        voice_data=store.status == "ready",
        active_session_id=await active_session_id(firestore, user.id),
    )


@router.get("/history", response_model=VoiceHistoryOut)
async def history(user: CurrentUser, service: VoiceSessionServiceDep) -> VoiceHistoryOut:
    """Your sessions, songs and recordings, newest first. Open even when the session flag is off."""
    return await _call(service.history(user))


@router.post("/sessions", response_model=SessionCreated)
async def create_session(body: SessionSetup, user: SessionUser, service: VoiceSessionServiceDep) -> SessionCreated:
    return await _call(service.create(user, body))


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: str, user: SessionUser, service: VoiceSessionServiceDep) -> SessionOut:
    return await _call(service.get(user, session_id))


@router.patch("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_session(
    session_id: str, body: SessionPatch, user: SessionUser, service: VoiceSessionServiceDep
) -> Response:
    await _call(service.patch(user, session_id, body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/next", response_model=NextSongOut | NextDoneOut)
async def next_song(session_id: str, user: SessionUser, service: VoiceSessionServiceDep) -> NextSongOut | NextDoneOut:
    return await _call(service.next_song(user, session_id))


@router.post("/sessions/{session_id}/skips", status_code=status.HTTP_204_NO_CONTENT)
async def skip_song(session_id: str, body: SkipIn, user: SessionUser, service: VoiceSessionServiceDep) -> Response:
    await _call(service.skip(user, session_id, body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{session_id}/attempts", response_model=AttemptOut)
async def add_attempt(
    session_id: str, body: AttemptIn, user: SessionUser, service: VoiceSessionServiceDep
) -> AttemptOut:
    return await _call(service.add_attempt(user, session_id, body))


@router.get("/sessions/{session_id}/result", response_model=ResultOut)
async def session_result(session_id: str, user: SessionUser, service: VoiceSessionServiceDep) -> ResultOut:
    return await _call(service.result(user, session_id))


@router.post("/sessions/{session_id}/adopt", response_model=VoiceProfileOut)
async def adopt_session(session_id: str, user: SessionUser, service: VoiceSessionServiceDep) -> VoiceProfileOut:
    """Make this session's result your voice profile, even at low confidence."""
    return await _call(service.adopt(user, session_id))


@router.patch("/attempts/{attempt_id}", response_model=AttemptPatchOut)
async def patch_attempt(
    attempt_id: str, body: AttemptPatchIn, user: CurrentUser, service: VoiceSessionServiceDep
) -> AttemptPatchOut:
    """ "Don't count this one" (``excluded: true``) or count it again. Open even when the session flag is off."""
    return await _call(service.set_excluded(user, attempt_id, body.excluded))


# ---------------------------------------------------------------- personal pool ("analyse my music")


@router.get("/personal-pool", response_model=PersonalPoolOut)
async def personal_pool(user: SessionUser, service: VoicePoolServiceDep) -> PersonalPoolOut:
    """Eligibility, the cap (``used`` of ``cap`` songs in the window) and your batches, newest first."""
    return await _call(service.status(user))


@router.post("/personal-pool", response_model=PersonalBatchCreated, status_code=status.HTTP_201_CREATED)
async def request_personal_pool(user: SessionUser, service: VoicePoolServiceDep) -> PersonalBatchCreated:
    """Queue up to ``cap`` of your best-known songs for analysis. Refusals: 403 ``not_eligible``, 429 ``cap``
    (with ``next_allowed_at``), 409 ``nothing_to_add`` / ``concurrent_request``, 503 ``budget_paused``."""
    return await _call(service.request(user))


# ---------------------------------------------------------------- recordings (private, consent-gated)


async def get_recording_service(
    firestore: FirestoreServiceDep, bucket: Annotated[Any, Depends(get_recordings_bucket)]
) -> VoiceRecordingService:
    return VoiceRecordingService(firestore, bucket)


RecordingServiceDep = Annotated[VoiceRecordingService, Depends(get_recording_service)]
MULTIPART_OVERHEAD = 64 * 1024  # form fields + boundaries around the file


class _BoundedBodyRoute(APIRoute):
    """Refuses a body over the recording cap with 413 *before* it is all read: by its declared
    Content-Length, and by counting bytes as they arrive when there is none (chunked)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            limit = vrs.MAX_BYTES + MULTIPART_OVERHEAD
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="recording_too_large")
            inner = request.receive
            received = 0

            async def receive() -> Message:
                nonlocal received
                message = await inner()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="recording_too_large"
                        )
                return message

            return await handler(Request(request.scope, receive, request._send))

        return bounded


recordings_router = APIRouter(route_class=_BoundedBodyRoute)


_FORM_FIELDS = frozenset({"file", "session_id", "attempt_n", "duration_s", "device"})


class RecordingCreated(BaseModel):
    recording_id: str


class RecordingsDeleted(BaseModel):
    deleted: int


class RecordingsCount(BaseModel):
    count: int


@recordings_router.post("/recordings", response_model=RecordingCreated, status_code=status.HTTP_201_CREATED)
async def upload_recording(
    request: Request,
    file: Annotated[UploadFile, File()],
    session_id: Annotated[str, Form(min_length=1, max_length=128)],
    attempt_n: Annotated[int, Form(ge=1, le=MAX_ATTEMPTS)],
    duration_s: Annotated[float, Form(ge=0, le=3600)],
    user: SessionUser,
    service: RecordingServiceDep,
    device: Annotated[str | None, Form(max_length=80)] = None,
) -> RecordingCreated:
    if set((await request.form()).keys()) - _FORM_FIELDS:  # cached by FastAPI's own parse
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown_form_fields")
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    try:
        recording_id = await service.upload(
            user.id,
            session_id=session_id,
            attempt_n=attempt_n,
            duration_s=duration_s,
            device=device,
            content_type=content_type,
            file=file.file,
        )
    except RecordingError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return RecordingCreated(recording_id=recording_id)


# Deleting and counting stay open to every signed-in user, even when the session flag is off.
@recordings_router.delete("/recordings", response_model=RecordingsDeleted)
async def delete_recordings(user: CurrentUser, service: RecordingServiceDep) -> RecordingsDeleted:
    return RecordingsDeleted(deleted=await service.delete_all_for_user(user.id))


@recordings_router.delete("/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recording(recording_id: str, user: CurrentUser, service: RecordingServiceDep) -> Response:
    """Delete one of your recordings (object, doc, attempt link). Idempotent; not yours: 404."""
    try:
        await service.delete_one(user.id, recording_id)
    except RecordingError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@recordings_router.get("/recordings/{recording_id}/audio")
async def recording_audio(
    recording_id: str, user: CurrentUser, settings: Settings, service: RecordingServiceDep
) -> StreamingResponse:
    """Stream one of your own recordings with its stored content type (no Range: the app downloads, then
    plays). Another user's recording, a missing one or a missing object: 404 ``recording_not_found``."""
    try:
        content_type, chunks = await service.open_own(user.id, recording_id, settings.voice_recordings_bucket)
    except RecordingError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return StreamingResponse(chunks, media_type=content_type)


@recordings_router.get("/recordings/count", response_model=RecordingsCount)
async def count_recordings(user: CurrentUser, service: RecordingServiceDep) -> RecordingsCount:
    return RecordingsCount(count=await service.count(user.id))


router.include_router(recordings_router)
