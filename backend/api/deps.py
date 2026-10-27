"""Dependency injection for API routes."""

import hmac
from datetime import date
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import BackendSettings, get_backend_settings
from backend.services.auth_service import AuthenticationError, AuthService, get_auth_service
from backend.services.catalog_lookup import CatalogLookup, get_catalog_lookup
from backend.services.catalog_store import CatalogStore, get_catalog_store
from backend.services.firestore_service import FirestoreService
from backend.services.gen_session import GenSessionError, get_gen_session_validator, is_gen_token
from backend.services.guest_claim_service import GuestClaimService
from backend.services.import_ingest_service import ImportIngestService
from backend.services.karaoke_log_service import KaraokeLogService
from backend.services.known_songs_service import KnownSongsService, get_known_songs_service
from backend.services.library_service import LibraryService
from backend.services.music_service_service import MusicServiceService, get_music_service_service
from backend.services.playlist_service import PlaylistService, get_playlist_service
from backend.services.quiz_service import QuizService, get_quiz_service
from backend.services.recommendation_service import RecommendationService, get_recommendation_service
from backend.services.recs_v2.context import HiddenKeysProvider
from backend.services.recs_v2.service import RecommendationServiceV2
from backend.services.song_feedback_service import SongFeedbackService
from backend.services.sync_service import SyncService, get_sync_service
from backend.services.user_data_service import UserDataService, get_user_data_service
from backend.services.voice_data_store import VoiceDataStore, get_voice_data_store
from backend.services.voice_pool_service import VoicePoolService
from backend.services.voice_profile_service import VoiceProfileService
from backend.services.voice_session_service import VoiceSessionService
from karaoke_decide.core.models import User
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

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


def _today() -> date:
    return date.today()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service_dep)],
) -> User:
    """Get the current authenticated user from a gen session token or a legacy decide JWT.

    Tokens without dots are gen session tokens (resolved to a decide profile);
    dotted tokens are legacy decide JWTs, accepted until settings.legacy_jwt_sunset.

    Raises:
        HTTPException: If not authenticated or token is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    if is_gen_token(token):
        try:
            identity = await get_gen_session_validator().validate(token)
        except GenSessionError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"gen_session_{e.reason}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e
        return await auth_service.resolve_gen_user(identity.email)

    if _today() > settings.legacy_jwt_sunset:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="legacy_token_expired",
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


async def get_admin_access(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    user: Annotated[User | None, Depends(get_optional_user)],
    x_admin_token: Annotated[str | None, Header()] = None,
) -> User | None:
    """Admin decide user, or a configured static ``X-Admin-Token`` (scripts). Otherwise 403.

    Returns the admin user, or None when the caller authenticated with a token.
    """
    if x_admin_token:
        supplied = x_admin_token.encode()
        matched = False
        for token in settings.admin_tokens:  # compare against every token: no early exit
            matched |= hmac.compare_digest(supplied, token.encode())
        if matched:
            return None
    if user is not None and user.is_admin:
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


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


async def get_catalog_store_dep() -> CatalogStore:
    """The process-wide recommendations catalog (tests override this)."""
    return get_catalog_store()


async def get_song_feedback_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> SongFeedbackService:
    """Song feedback ("Not for me" / "Love it") service."""
    return SongFeedbackService(firestore)


async def get_hidden_keys_provider(
    feedback: Annotated[SongFeedbackService, Depends(get_song_feedback_service)],
) -> HiddenKeysProvider:
    """Source of a user's hidden ("Not for me") song keys."""
    return feedback


async def get_recommendation_service_v2_dep(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    store: Annotated[CatalogStore, Depends(get_catalog_store_dep)],
    feedback: Annotated[HiddenKeysProvider, Depends(get_hidden_keys_provider)],
) -> RecommendationServiceV2:
    """Get the v2 (catalog.db) recommendation service."""
    return RecommendationServiceV2(firestore, store, feedback, VoiceProfileService(firestore))


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


async def get_guest_claim_service_dep(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    auth_service: Annotated[AuthService, Depends(get_auth_service_dep)],
) -> GuestClaimService:
    """Get guest claim service instance."""
    return GuestClaimService(firestore, auth_service)


async def get_import_ingest_service_dep(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> ImportIngestService:
    """The one write path every music import goes through."""
    return ImportIngestService(firestore)


async def get_catalog_lookup_dep() -> CatalogLookup:
    """Artist + title matching against catalog.db (tests override this)."""
    return get_catalog_lookup()


async def get_karaoke_log_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    auth_service: Annotated[AuthService, Depends(get_auth_service_dep)],
    catalog: Annotated[CatalogLookup, Depends(get_catalog_lookup_dep)],
) -> KaraokeLogService:
    """Karaoke log service; partner writes resolve (or create) the profile by gen email."""

    async def resolve_user(email: str) -> str:
        return (await auth_service.resolve_gen_user(email)).id

    async def find_user(email: str) -> str | None:  # lookup only: never creates a profile
        doc = await firestore.get_document(AuthService.USERS_COLLECTION, auth_service._hash_email(email.lower()))
        return doc.get("user_id") if doc else None

    return KaraokeLogService(firestore, catalog=catalog, resolve_user=resolve_user, find_user=find_user)


async def get_library_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    feedback: Annotated[SongFeedbackService, Depends(get_song_feedback_service)],
    playlists: Annotated[PlaylistService, Depends(get_playlist_service_dep)],
    store: Annotated[CatalogStore, Depends(get_catalog_store_dep)],
) -> LibraryService:
    """The Me library aggregate (karaoke log + loves + setlists + catalog facets)."""
    return LibraryService(firestore, feedback, playlists, store)


async def get_voice_profile_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
) -> VoiceProfileService:
    return VoiceProfileService(firestore)


def get_voice_store() -> VoiceDataStore:
    """The lifespan-loaded voice.db store (tests override this)."""
    return get_voice_data_store()


async def get_voice_session_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    voice: Annotated[VoiceDataStore, Depends(get_voice_store)],
    catalog: Annotated[CatalogStore, Depends(get_catalog_store_dep)],
    # SongFeedbackService (hidden songs + "Love it"); tests override get_hidden_keys_provider with a fake.
    feedback: Annotated[SongFeedbackService, Depends(get_hidden_keys_provider)],
    recs: Annotated[RecommendationServiceV2, Depends(get_recommendation_service_v2_dep)],
) -> VoiceSessionService:
    return VoiceSessionService(firestore, voice, catalog, feedback, VoiceProfileService(firestore), recs)


async def get_voice_pool_service(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    voice: Annotated[VoiceDataStore, Depends(get_voice_store)],
    catalog: Annotated[CatalogStore, Depends(get_catalog_store_dep)],
    feedback: Annotated[HiddenKeysProvider, Depends(get_hidden_keys_provider)],
    settings: Annotated[BackendSettings, Depends(get_settings)],
) -> VoicePoolService:
    return VoicePoolService(firestore, voice, catalog, feedback, settings)


_bigquery_catalog_instance: BigQueryCatalogService | None = None


def get_bigquery_catalog() -> BigQueryCatalogService | None:
    """Get BigQuery catalog service instance (singleton).

    Returns None if BigQuery credentials are not available (e.g., in tests).
    """
    global _bigquery_catalog_instance
    if _bigquery_catalog_instance is None:
        try:
            _bigquery_catalog_instance = BigQueryCatalogService()
        except Exception:
            # Credentials not available (e.g., in CI tests)
            return None
    return _bigquery_catalog_instance


async def get_user_data_service_dep(
    firestore: Annotated[FirestoreService, Depends(get_firestore)],
    bigquery_catalog: Annotated[BigQueryCatalogService | None, Depends(get_bigquery_catalog)],
) -> UserDataService:
    """Get user data service instance."""
    return get_user_data_service(firestore, bigquery_catalog)


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(get_verified_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]
AdminAccess = Annotated[User | None, Depends(get_admin_access)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
Settings = Annotated[BackendSettings, Depends(get_settings)]
FirestoreServiceDep = Annotated[FirestoreService, Depends(get_firestore)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service_dep)]
MusicServiceServiceDep = Annotated[MusicServiceService, Depends(get_music_service_service_dep)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service_dep)]
QuizServiceDep = Annotated[QuizService, Depends(get_quiz_service_dep)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service_dep)]
RecommendationServiceV2Dep = Annotated[RecommendationServiceV2, Depends(get_recommendation_service_v2_dep)]
PlaylistServiceDep = Annotated[PlaylistService, Depends(get_playlist_service_dep)]
KnownSongsServiceDep = Annotated[KnownSongsService, Depends(get_known_songs_service_dep)]
UserDataServiceDep = Annotated[UserDataService, Depends(get_user_data_service_dep)]
GuestClaimServiceDep = Annotated[GuestClaimService, Depends(get_guest_claim_service_dep)]
KaraokeLogServiceDep = Annotated[KaraokeLogService, Depends(get_karaoke_log_service)]
LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]
ImportIngestServiceDep = Annotated[ImportIngestService, Depends(get_import_ingest_service_dep)]
VoiceProfileServiceDep = Annotated[VoiceProfileService, Depends(get_voice_profile_service)]
VoiceDataStoreDep = Annotated[VoiceDataStore, Depends(get_voice_store)]
VoiceSessionServiceDep = Annotated[VoiceSessionService, Depends(get_voice_session_service)]
VoicePoolServiceDep = Annotated[VoicePoolService, Depends(get_voice_pool_service)]
