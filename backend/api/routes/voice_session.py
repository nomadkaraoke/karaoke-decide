"""Voice session routes: ``/api/voice/*`` (mounted at ``/voice``). Signed-in users.

Behind a flag: a session needs ``settings.voice_session_open`` or an admin user, else
403 ``voice_session_disabled``. Sessions are user-scoped (another user's id is 404).
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.api.deps import CurrentUser, Settings, VoiceDataStoreDep, VoiceSessionServiceDep
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
from backend.services.voice_data_store import VoiceDataUnavailable
from backend.services.voice_session_service import VoiceSessionError
from karaoke_decide.core.models import User

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
