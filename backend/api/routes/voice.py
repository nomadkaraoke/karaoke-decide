"""Voice profile routes: ``/api/my/voice`` (mounted at ``/my``). Bearer gen session token."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from backend.api.deps import CurrentUser, VoiceProfileServiceDep
from backend.services.voice_profile_service import (
    VoiceProfileIn,
    VoiceProfileOut,
    VoiceProfileResponse,
    VoiceProfileUserNotFoundError,
)

router = APIRouter()


@router.get("/voice", response_model=VoiceProfileResponse)
async def get_voice(user: CurrentUser, service: VoiceProfileServiceDep) -> VoiceProfileResponse:
    return VoiceProfileResponse(profile=await service.get(user.id))


@router.put("/voice")
async def put_voice(body: VoiceProfileIn, user: CurrentUser, service: VoiceProfileServiceDep) -> VoiceProfileOut:
    try:
        return await service.put(user.id, body)
    except VoiceProfileUserNotFoundError:
        raise HTTPException(status_code=404, detail="not_found") from None


@router.delete("/voice", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(user: CurrentUser, service: VoiceProfileServiceDep) -> Response:
    await service.delete(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
