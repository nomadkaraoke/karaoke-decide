"""Dependency injection for API routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import BackendSettings, get_backend_settings
from karaoke_decide.core.models import User

# Security scheme
security = HTTPBearer(auto_error=False)


async def get_settings() -> BackendSettings:
    """Get application settings."""
    return get_backend_settings()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
) -> User:
    """Get the current authenticated user from the JWT token.

    Raises:
        HTTPException: If not authenticated or token is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # TODO: Validate JWT token and fetch user
    # token = credentials.credentials
    # user = await validate_token_and_get_user(token, settings)

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Authentication not yet implemented",
    )


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
) -> User | None:
    """Get the current user if authenticated, None otherwise."""
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, settings)
    except HTTPException:
        return None


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
Settings = Annotated[BackendSettings, Depends(get_settings)]
