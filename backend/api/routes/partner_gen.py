"""gen -> decide partner routes (server to server).

``DELETE /api/partner/gen/users`` (mounted at ``/partner/gen``): gen's account deletion erases
everything decide holds for the user (``user_deletion.delete_all_user_data``). Header
``X-Partner-Secret`` must equal the ``DECIDE_GEN_PARTNER_SECRET`` setting
(``hmac.compare_digest``): a missing or wrong secret is 401 and an unset one 503. gen retries on
any failure, and the erasure is idempotent, so a second call returns ``{"deleted": {}}``.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings
from backend.api.routes.admin_voice import get_recordings_bucket
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
