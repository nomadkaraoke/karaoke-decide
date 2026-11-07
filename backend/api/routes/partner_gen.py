"""gen -> decide partner routes (server to server).

``DELETE /api/partner/gen/users`` (mounted at ``/partner/gen``): gen's account deletion erases
everything decide holds for the user (``user_deletion.delete_all_user_data``). Header
``X-Partner-Secret`` must equal the ``DECIDE_GEN_PARTNER_SECRET`` setting
(``hmac.compare_digest``): a missing or wrong secret is 401 and an unset one 503. gen retries on
any failure, and the erasure is idempotent, so a second call returns ``{"deleted": {}}``.

``GET /api/partner/gen/spotify-search``: live Spotify track search (``services/spotify_search.py``)
that gen's match judge uses to confirm a typed song exists. It is partner-only, because the Spotify
quota is shared and a public proxy could drain it.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.services.spotify_search import get_spotify_search_service
from backend.services.user_deletion import delete_all_user_data

logger = logging.getLogger(__name__)

router = APIRouter()


class DeleteUserBody(BaseModel):
    email: str


async def require_gen_partner(settings: Settings, x_partner_secret: Annotated[str | None, Header()] = None) -> None:
    expected = settings.gen_partner_secret
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="partner_disabled")
    if not hmac.compare_digest((x_partner_secret or "").encode(), expected.encode()):
        logger.warning("gen partner: missing or wrong X-Partner-Secret")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="partner_auth")


@router.get("/spotify-search", dependencies=[Depends(require_gen_partner)])
async def spotify_search(
    settings: Settings,
    q: Annotated[str, Query(min_length=2, max_length=300)],
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
) -> dict[str, Any]:
    """Live Spotify track search for gen's match judge: ``{"tracks": [...], "available": bool}``.

    ``available: false`` means "no evidence" (not configured, rate-limit breaker open, or Spotify
    failed). It is never an HTTP error, so gen degrades instead of retrying.
    """
    svc = get_spotify_search_service(settings.spotify_client_id, settings.spotify_client_secret)
    result = await svc.search_tracks(q, limit)
    return {"tracks": result.tracks, "available": result.available}


@router.delete("/users", dependencies=[Depends(require_gen_partner)])
async def delete_user(
    body: DeleteUserBody,
    fs: FirestoreServiceDep,
    bucket: Annotated[Any, Depends(get_recordings_bucket)],
) -> dict[str, dict[str, int]]:
    """Erase all decide data for the email; ``{"deleted": {collection: count}}`` (``{}`` when nothing was there)."""
    deleted = await delete_all_user_data(fs, bucket, body.email)
    logger.info("gen partner: deleted user data (%d stores touched)", len(deleted))
    return {"deleted": deleted}
