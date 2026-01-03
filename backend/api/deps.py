"""Dependency injection for API routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import BackendSettings, get_backend_settings
from backend.services.auth_service import AuthenticationError, AuthService, get_auth_service
from backend.services.firestore_service import FirestoreService
from backend.services.known_songs_service import KnownSongsService, get_known_songs_service
from backend.services.music_service_service import MusicServiceService, get_music_service_service
from backend.services.playlist_service import PlaylistService, get_playlist_service
from backend.services.quiz_service import QuizService, get_quiz_service
from backend.services.recommendation_service import RecommendationService, get_recommendation_service
from backend.services.sync_service import SyncService, get_sync_service
from backend.services.user_data_service import UserDataService, get_user_data_service
from karaoke_decide.core.models import User

# Security scheme
security = HTTPBearer(auto_error=False)


async def get_settings() -> BackendSettings:
    """Get application settings."""
    return get_backend_settings()


async def get_firestore(
    settings: Annotated[BackendSettings, Depends(get_settings)],
) -> FirestoreService:
    """Get Firestore service instance."""
    return FirestoreService(settings)


async def get_auth_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> AuthService:
    """Get auth service instance."""
    return get_auth_service(settings, firestore)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service_dep)],
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

    try:
        # Validate JWT token
        claims = auth_service.validate_jwt(credentials.credentials)
        user_id = claims.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Fetch user from Firestore
        user = await auth_service.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service_dep)],
) -> User | None:
    """Get the current user if authenticated, None otherwise."""
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, settings, auth_service)
    except HTTPException:
        return None


async def get_verified_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current user only if they are verified (not a guest).

    Raises:
        HTTPException: If user is a guest (not verified).
    """
    if user.is_guest:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required. Please verify your email to use this feature.",
        )
    return user


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current user only if they are an admin.

    Raises:
        HTTPException: If user is not an admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_music_service_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> MusicServiceService:
    """Get music service service instance."""
    return get_music_service_service(settings, firestore)


async def get_sync_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    music_service: Annotated[MusicServiceService, Depends(get_music_service_service_dep)],
) -> SyncService:
    """Get sync service instance."""
    return get_sync_service(settings, firestore, music_service)


async def get_quiz_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> QuizService:
    """Get quiz service instance."""
    return get_quiz_service(settings, firestore)


async def get_recommendation_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> RecommendationService:
    """Get recommendation service instance."""
    return get_recommendation_service(settings, firestore)


async def get_playlist_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> PlaylistService:
    """Get playlist service instance."""
    return get_playlist_service(settings, firestore)


async def get_known_songs_service_dep(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> KnownSongsService:
    """Get known songs service instance."""
    return get_known_songs_service(settings, firestore)


async def get_user_data_service_dep(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> UserDataService:
    """Get user data service instance."""
    return get_user_data_service(firestore)


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(get_verified_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
Settings = Annotated[BackendSettings, Depends(get_settings)]
FirestoreServiceDep = Annotated[FirestoreService, Depends(get_firestore)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service_dep)]
MusicServiceServiceDep = Annotated[MusicServiceService, Depends(get_music_service_service_dep)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service_dep)]
QuizServiceDep = Annotated[QuizService, Depends(get_quiz_service_dep)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service_dep)]
PlaylistServiceDep = Annotated[PlaylistService, Depends(get_playlist_service_dep)]
KnownSongsServiceDep = Annotated[KnownSongsService, Depends(get_known_songs_service_dep)]
UserDataServiceDep = Annotated[UserDataService, Depends(get_user_data_service_dep)]
