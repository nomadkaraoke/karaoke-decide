"""Get this made (mounted at ``/my/get-made``): ``GET ""`` lists the user's songs to get made;
``POST|DELETE "/{key}/dismiss"`` dismisses / restores one (204).

``key`` is a library key (a catalog ``song_key`` or ``"u:" + norm``) holding ``/``, ``|``, ``#`` ...,
so it is matched with the ``path`` converter and decoded once from the raw path
(``my_songs._song_key``); clients percent-encode it.
"""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request, Response, status

from backend.api.deps import CurrentUser, GetMadeServiceDep
from backend.services.catalog_store import CatalogUnavailable
from backend.services.get_made_service import GetMadeResponse

router = APIRouter()  # /my/get-made

_MARKER = "/get-made/"
_ACTION = "/dismiss"
MAX_KEY_LEN = 512


def _key(request: Request, key: str) -> str:
    """The exact key, percent-decoded once from the raw request path."""
    raw = request.scope.get("raw_path")
    if raw:
        text = raw.decode("latin-1").split("?", 1)[0]
        start = text.find(_MARKER)
        if start >= 0:
            body = text[start + len(_MARKER) :]
            if body.endswith(_ACTION):
                body = body[: -len(_ACTION)]
            key = unquote(body)
    if not key:
        raise HTTPException(status_code=404, detail="unknown_song")
    if len(key) > MAX_KEY_LEN:
        raise HTTPException(status_code=400, detail="invalid")
    return key


@router.get("", response_model=GetMadeResponse)
async def get_made(user: CurrentUser, service: GetMadeServiceDep) -> GetMadeResponse:
    try:
        return await service.list(user.id)
    except CatalogUnavailable:
        raise HTTPException(status_code=503, detail="warming_up", headers={"Retry-After": "30"}) from None


@router.post("/{key:path}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(request: Request, key: str, user: CurrentUser, service: GetMadeServiceDep) -> Response:
    await service.dismiss(user.id, _key(request, key))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{key:path}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def undismiss(request: Request, key: str, user: CurrentUser, service: GetMadeServiceDep) -> Response:
    await service.undismiss(user.id, _key(request, key))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
