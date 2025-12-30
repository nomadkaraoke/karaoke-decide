"""Authentication routes."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from backend.api.deps import AuthServiceDep, CurrentUser
from backend.services.auth_service import AuthenticationError

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
async def request_magic_link(
    request: MagicLinkRequest,
    auth_service: AuthServiceDep,
) -> MagicLinkResponse:
    """Request a magic link to be sent to the user's email.

    This endpoint generates a secure token, stores it with a 15-minute TTL,
    and sends an email with a link to verify the token.

    In development mode (when SendGrid is not configured), the magic link
    is logged to the console instead of being sent via email.
    """
    success = await auth_service.send_magic_link(request.email)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send magic link email",
        )

    return MagicLinkResponse(message="If an account exists for this email, you will receive a magic link shortly.")


@router.post("/verify", response_model=AuthResponse)
async def verify_magic_link(
    request: VerifyTokenRequest,
    auth_service: AuthServiceDep,
) -> AuthResponse:
    """Verify a magic link token and return an access token.

    This endpoint:
    1. Validates the magic link token
    2. Creates a new user if one doesn't exist for this email
    3. Returns a JWT access token for authentication
    """
    try:
        # Verify the magic link token
        email = await auth_service.verify_magic_link(request.token)

        # Get or create the user
        user = await auth_service.get_or_create_user(email)

        # Generate JWT
        access_token, expires_in = auth_service.generate_jwt(user)

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
        )

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except ValueError as e:
        # JWT secret not configured
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_endpoint(user: CurrentUser) -> UserResponse:
    """Get the current authenticated user.

    Requires a valid Bearer token in the Authorization header.
    """
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/logout")
async def logout(user: CurrentUser) -> dict[str, str]:
    """Log out the current user.

    This is a stateless logout - the client should discard their token.
    The server does not maintain a token blacklist.
    """
    # Stateless logout - just acknowledge the request
    # The client is responsible for discarding the token
    return {"message": "Successfully logged out"}
