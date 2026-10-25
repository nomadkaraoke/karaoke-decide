"""``GET /api/my/library`` (mounted at ``/my/library``): the signed-in user's Me library.

Returns an ``ETag`` (the sha1 of the items); a matching ``If-None-Match`` gets 304 with no body.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Response

from backend.api.deps import CurrentUser, LibraryServiceDep
from backend.services.library_service import LibraryResponse

router = APIRouter()  # /my/library


def _matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(part.strip().removeprefix("W/").strip('"') == etag for part in header.split(","))


@router.get("", response_model=LibraryResponse, responses={304: {"description": "Not modified"}})
async def get_library(
    user: CurrentUser,
    service: LibraryServiceDep,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    library = await service.get_library(user.id)
    headers = {"ETag": f'"{library.etag}"'}
    if _matches(if_none_match, library.etag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return library
