"""Authentication routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

router = APIRouter()


class MagicLinkRequest(BaseModel):
    """Request to send a magic link."""

    email: EmailStr


class MagicLinkResponse(BaseModel):
    """Response after requesting a magic link."""

    message: str


class VerifyTokenRequest(BaseModel):
    """Request to verify a magic link token."""

    token: str


class AuthResponse(BaseModel):
    """Response with auth token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """Current user information."""

    id: str
    email: str
    display_name: str | None = None


@router.post("/magic-link", response_model=MagicLinkResponse)
async def request_magic_link(request: MagicLinkRequest) -> MagicLinkResponse:
    """Request a magic link to be sent to the user's email."""
    # TODO: Implement magic link sending
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/verify", response_model=AuthResponse)
async def verify_magic_link(request: VerifyTokenRequest) -> AuthResponse:
    """Verify a magic link token and return an access token."""
    # TODO: Implement token verification
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/me", response_model=UserResponse)
async def get_current_user() -> UserResponse:
    """Get the current authenticated user."""
    # TODO: Implement with auth dependency
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/logout")
async def logout() -> dict[str, str]:
    """Invalidate the current session."""
    # TODO: Implement session invalidation
    raise HTTPException(status_code=501, detail="Not implemented")
