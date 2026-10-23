"""OIDC verification for /internal/* routes (Cloud Tasks callbacks).

The public service must not trust X-CloudTasks-* headers (they are forgeable).
Instead we verify the Google-signed OIDC token that Cloud Tasks attaches.
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from backend.api.deps import Settings
from backend.config import BackendSettings

logger = logging.getLogger(__name__)


async def verify_internal_request(request: Request, settings: BackendSettings) -> bool:
    """Return True if the request carries a valid OIDC token from an allowed invoker."""
    if not settings.is_production:
        return True

    if not settings.cloud_run_url:
        # An empty audience would make google-auth skip the audience check entirely.
        logger.warning("Internal request rejected: CLOUD_RUN_URL not configured")
        return False

    try:
        # Lazy: google.auth.transport.requests pulls in cryptography (API cold start budget).
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        auth = request.headers.get("Authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            logger.warning("Internal request rejected: missing bearer token")
            return False

        claims = await run_in_threadpool(
            id_token.verify_oauth2_token,
            token.strip(),
            google_requests.Request(),
            audience=settings.cloud_run_url,
        )
        if not claims.get("email_verified"):
            logger.warning("Internal request rejected: email not verified")
            return False
        if claims.get("email") not in settings.internal_invoker_emails:
            logger.warning("Internal request rejected: unexpected invoker %s", claims.get("email"))
            return False
        return True
    except Exception as e:
        logger.warning("Internal request rejected: token verification failed: %s", e)
        return False


async def _require_internal_caller(request: Request, settings: Settings) -> None:
    if not await verify_internal_request(request, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: not a valid internal caller",
        )


InternalCaller = Depends(_require_internal_caller)
InternalCallerDep = Annotated[None, InternalCaller]
