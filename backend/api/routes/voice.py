"""Voice profile routes: ``/api/my/voice`` (mounted at ``/my``). Bearer gen session token."""

from __future__ import annotations

from typing import Self

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import model_validator

from backend.api.deps import CurrentUser, FirestoreServiceDep, VoiceProfileServiceDep
from backend.models.voice_session import VoiceProfileResponse
from backend.services.voice_profile_service import (
    VoiceProfileIn,
    VoiceProfileOut,
    VoiceProfileUserNotFoundError,
)
from backend.services.voice_session_service import latest_session_summary

router = APIRouter()


@router.get("/voice", response_model=VoiceProfileResponse)
async def get_voice(
    user: CurrentUser, service: VoiceProfileServiceDep, firestore: FirestoreServiceDep
) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        profile=await service.get(user.id),
        mic=await service.get_mic(user.id),
        latest_session=await latest_session_summary(firestore, user.id),
    )


class VoiceProfilePutIn(VoiceProfileIn):
    """``PUT /api/my/voice``: the app's mic / self-report profiles. ``method: 'session'`` profiles are
    saved only by a voice session's result (``VoiceSessionService``), never through this route."""

    @model_validator(mode="after")
    def _not_session(self) -> Self:
        if self.method == "session":
            raise ValueError("method 'session' is set only by a voice session")
        return self


@router.put("/voice")
async def put_voice(body: VoiceProfilePutIn, user: CurrentUser, service: VoiceProfileServiceDep) -> VoiceProfileOut:
    try:
        return await service.put(user.id, body)
    except VoiceProfileUserNotFoundError:
        raise HTTPException(status_code=404, detail="not_found") from None


@router.delete("/voice", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(user: CurrentUser, service: VoiceProfileServiceDep) -> Response:
    await service.delete(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
