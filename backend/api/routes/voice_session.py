"""Voice session routes: ``/api/voice/*`` (mounted at ``/voice``). Signed-in users.

Behind a flag: a session needs ``settings.voice_session_open`` or an admin user, else
403 ``voice_session_disabled``. Sessions are user-scoped (another user's id is 404).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import Message

from backend.api.deps import CurrentUser, FirestoreServiceDep, Settings, VoiceDataStoreDep, VoiceSessionServiceDep
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.models.voice_session import (
    AttemptIn,
    AttemptOut,
    NextDoneOut,
    NextSongOut,
    ResultOut,
    SessionCreated,
    SessionPatch,
    SessionSetup,
    SkipIn,
    VoiceFlagsOut,
)
from backend.services import voice_recording_service as vrs
from backend.services.voice_data_store import VoiceDataUnavailable
from backend.services.voice_recording_service import RecordingError, VoiceRecordingService
from backend.services.voice_session_service import VoiceSessionError
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
    except VoiceDataUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="warming_up") from e
    except VoiceSessionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.get("/flags", response_model=VoiceFlagsOut)
async def flags(user: CurrentUser, settings: Settings, store: VoiceDataStoreDep) -> VoiceFlagsOut:
    return VoiceFlagsOut(
        session_enabled=_enabled(settings, user), is_admin=user.is_admin, voice_data=store.status == "ready"
    )


@router.post("/sessions", response_model=SessionCreated)
async def create_session(body: SessionSetup, user: SessionUser, service: VoiceSessionServiceDep) -> SessionCreated:
    return await _call(service.create(user, body))


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


@recordings_router.get("/recordings/count", response_model=RecordingsCount)
async def count_recordings(user: CurrentUser, service: RecordingServiceDep) -> RecordingsCount:
    return RecordingsCount(count=await service.count(user.id))


router.include_router(recordings_router)
