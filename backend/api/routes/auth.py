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
    email: str | None = None
    display_name: str | None = None
    is_guest: bool = False
    is_admin: bool = False


class UpdateProfileRequest(BaseModel):
    """Request to update user profile."""

    display_name: str | None = None


class UpgradeGuestRequest(BaseModel):
    """Request to upgrade a guest user to verified."""

    email: EmailStr


class UpgradeGuestResponse(BaseModel):
    """Response after requesting guest upgrade."""

    message: str


@router.post("/guest", response_model=AuthResponse)
async def create_guest_session(
    auth_service: AuthServiceDep,
) -> AuthResponse:
    """Create a guest/anonymous user session.

    This allows users to try the app (quiz, recommendations) without
    providing an email. Guest users cannot connect music services
    until they verify their email.

    Guest sessions last 30 days.
    """
    try:
        # Create guest user
        guest_user = await auth_service.create_guest_user()

        # Generate JWT for guest
        access_token, expires_in = auth_service.generate_guest_jwt(guest_user)

        return AuthResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
        )

    except ValueError as e:
        # JWT secret not configured
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/upgrade", response_model=UpgradeGuestResponse)
async def request_guest_upgrade(
    request: UpgradeGuestRequest,
    user: CurrentUser,
    auth_service: AuthServiceDep,
) -> UpgradeGuestResponse:
    """Request to upgrade a guest account to a verified account.

    Sends a magic link to the provided email. When the user verifies,
    their guest data will be migrated to the verified account.

    If the email already has an account, the guest data will be merged.
    """
    if not user.is_guest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is already verified",
        )

    try:
        # Store the guest user ID in the magic link so we can upgrade on verify
        token = auth_service.generate_magic_link_token()
        await auth_service.store_magic_link(request.email, token)

        # Store the guest_user_id association with this magic link
        await auth_service.firestore.update_document(
            auth_service.MAGIC_LINKS_COLLECTION,
            token,
            {"guest_user_id": user.id},
        )

        success = await auth_service.email_service.send_magic_link(request.email, token)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send verification email. Please try again later.",
            )

        return UpgradeGuestResponse(message="Verification email sent. Check your inbox to complete the upgrade.")
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


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
    try:
        success = await auth_service.send_magic_link(request.email)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send magic link email. Please try again later.",
            )

        return MagicLinkResponse(message="If an account exists for this email, you will receive a magic link shortly.")
    except RuntimeError as e:
        # Email service configuration error (e.g., SendGrid not configured in production)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )


@router.post("/verify", response_model=AuthResponse)
async def verify_magic_link(
    request: VerifyTokenRequest,
    auth_service: AuthServiceDep,
) -> AuthResponse:
    """Verify a magic link token and return an access token.

    This endpoint:
    1. Validates the magic link token
    2. If this was a guest upgrade request, migrates guest data to the verified account
    3. Creates a new user if one doesn't exist for this email
    4. Returns a JWT access token for authentication
    """
    try:
        # Get the magic link document before verification (to check for guest upgrade)
        magic_link_doc = await auth_service.firestore.get_document(auth_service.MAGIC_LINKS_COLLECTION, request.token)
        guest_user_id = magic_link_doc.get("guest_user_id") if magic_link_doc else None

        # Verify the magic link token
        email = await auth_service.verify_magic_link(request.token)

        # Handle guest upgrade if applicable
        if guest_user_id:
            user = await auth_service.upgrade_guest_to_verified(guest_user_id, email)
            if user is None:
                # Guest not found, fall back to normal flow
                user = await auth_service.get_or_create_user(email)
        else:
            # Normal flow: get or create the user
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
        is_guest=user.is_guest,
        is_admin=user.is_admin,
    )


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    user: CurrentUser,
    auth_service: AuthServiceDep,
) -> UserResponse:
    """Update the current user's profile.

    Allows updating display name and other profile settings.
    Requires a valid Bearer token in the Authorization header.
    """
    updated_user = await auth_service.update_user_profile(
        user_id=user.id,
        display_name=request.display_name,
    )

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse(
        id=updated_user.id,
        email=updated_user.email,
        display_name=updated_user.display_name,
        is_guest=updated_user.is_guest,
        is_admin=updated_user.is_admin,
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
