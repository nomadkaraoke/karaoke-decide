"""Music service connection management.

Handles OAuth flows, token management, and CRUD operations for
connected music services (Spotify, Last.fm).
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from karaoke_decide.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from karaoke_decide.core.models import MusicService
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.spotify import SpotifyClient


class MusicServiceError(Exception):
    """Error during music service operations."""

    pass


class MusicServiceService:
    """Service for managing music service connections.

    Handles:
    - OAuth state management (CSRF protection)
    - Service connection CRUD
    - Token refresh for Spotify
    """

    SERVICES_COLLECTION = "music_services"
    OAUTH_STATES_COLLECTION = "oauth_states"
    STATE_EXPIRATION_MINUTES = 10
    TOKEN_REFRESH_BUFFER_MINUTES = 5  # Refresh if expiring within this window

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        spotify_client: SpotifyClient | None = None,
        lastfm_client: LastFmClient | None = None,
    ):
        """Initialize the music service service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for persistence.
            spotify_client: Optional Spotify client (created lazily if not provided).
            lastfm_client: Optional Last.fm client (created lazily if not provided).
        """
        self.settings = settings
        self.firestore = firestore
        self._spotify_client = spotify_client
        self._lastfm_client = lastfm_client

    @property
    def spotify(self) -> SpotifyClient:
        """Get or create Spotify client."""
        if self._spotify_client is None:
            self._spotify_client = SpotifyClient(self.settings)
        return self._spotify_client

    @property
    def lastfm(self) -> LastFmClient:
        """Get or create Last.fm client."""
        if self._lastfm_client is None:
            self._lastfm_client = LastFmClient(self.settings)
        return self._lastfm_client

    # -------------------------------------------------------------------------
    # OAuth State Management
    # -------------------------------------------------------------------------

    def _generate_oauth_state(self) -> str:
        """Generate secure OAuth state token.

        Returns:
            URL-safe random token string.
        """
        return secrets.token_urlsafe(32)

    def _get_service_id(self, user_id: str, service_type: str) -> str:
        """Generate document ID for a service connection.

        Args:
            user_id: User ID.
            service_type: Service type (spotify, lastfm).

        Returns:
            Document ID in format {user_id}_{service_type}.
        """
        return f"{user_id}_{service_type}"

    async def create_oauth_state(self, user_id: str, service_type: str) -> str:
        """Create and store OAuth state for CSRF protection.

        Args:
            user_id: User initiating OAuth flow.
            service_type: Service type (e.g., "spotify").

        Returns:
            State token to include in OAuth redirect.
        """
        state = self._generate_oauth_state()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.STATE_EXPIRATION_MINUTES)

        await self.firestore.set_document(
            self.OAUTH_STATES_COLLECTION,
            state,
            {
                "user_id": user_id,
                "service_type": service_type,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
        )

        return state

    async def verify_oauth_state(self, state: str) -> dict[str, str] | None:
        """Verify and consume OAuth state atomically.

        Uses an atomic delete-and-return to prevent TOCTOU race conditions
        where two concurrent callbacks could both pass verification.

        Args:
            state: State token from OAuth callback.

        Returns:
            Dict with user_id and service_type if valid, None otherwise.
        """
        # Atomically delete and retrieve the state document
        # This ensures the state can only be consumed once
        doc = await self.firestore.delete_document_atomically(self.OAUTH_STATES_COLLECTION, state)

        if doc is None:
            return None

        # Check expiration (state was already deleted, so just validate)
        expires_at = datetime.fromisoformat(doc["expires_at"])
        if datetime.now(UTC) > expires_at:
            return None

        return {
            "user_id": doc["user_id"],
            "service_type": doc["service_type"],
        }

    # -------------------------------------------------------------------------
    # Service CRUD Operations
    # -------------------------------------------------------------------------

    async def get_user_services(self, user_id: str) -> list[MusicService]:
        """Get all connected services for a user.

        Args:
            user_id: User ID.

        Returns:
            List of connected MusicService records.
        """
        docs = await self.firestore.query_documents(
            self.SERVICES_COLLECTION,
            filters=[("user_id", "==", user_id)],
        )

        return [self._doc_to_music_service(doc) for doc in docs]

    async def get_service(self, user_id: str, service_type: str) -> MusicService | None:
        """Get a specific service connection.

        Args:
            user_id: User ID.
            service_type: Service type (spotify, lastfm).

        Returns:
            MusicService if found, None otherwise.
        """
        service_id = self._get_service_id(user_id, service_type)
        doc = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)

        if doc is None:
            return None

        return self._doc_to_music_service(doc)

    async def create_or_update_spotify_service(
        self,
        user_id: str,
        tokens: dict[str, Any],
        profile: dict[str, Any],
    ) -> MusicService:
        """Create or update Spotify service connection.

        Args:
            user_id: User ID.
            tokens: Token response from Spotify (access_token, refresh_token, expires_in).
            profile: User profile from Spotify /me endpoint.

        Returns:
            Created or updated MusicService.
        """
        service_id = self._get_service_id(user_id, "spotify")
        now = datetime.now(UTC)

        # Calculate token expiration
        expires_in = tokens.get("expires_in", 3600)
        token_expires_at = now + timedelta(seconds=expires_in)

        # Get Spotify user info
        spotify_user_id = profile.get("id", "")
        spotify_username = profile.get("display_name") or profile.get("id", "")

        # Check if service already exists
        existing = await self.get_service(user_id, "spotify")

        if existing:
            # Update existing service
            update_data = {
                "access_token": tokens.get("access_token"),
                "token_expires_at": token_expires_at.isoformat(),
                "updated_at": now.isoformat(),
            }

            # Only update refresh_token if provided (Spotify doesn't always return it)
            if tokens.get("refresh_token"):
                update_data["refresh_token"] = tokens["refresh_token"]

            await self.firestore.update_document(
                self.SERVICES_COLLECTION,
                service_id,
                update_data,
            )

            return MusicService(
                id=service_id,
                user_id=user_id,
                service_type="spotify",
                service_user_id=existing.service_user_id,
                service_username=existing.service_username,
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token") or existing.refresh_token,
                token_expires_at=token_expires_at,
                last_sync_at=existing.last_sync_at,
                sync_status=existing.sync_status,
                sync_error=existing.sync_error,
                tracks_synced=existing.tracks_synced,
                created_at=existing.created_at,
                updated_at=now,
            )

        # Create new service
        service_data = {
            "id": service_id,
            "user_id": user_id,
            "service_type": "spotify",
            "service_user_id": spotify_user_id,
            "service_username": spotify_username,
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "token_expires_at": token_expires_at.isoformat(),
            "last_sync_at": None,
            "sync_status": "idle",
            "sync_error": None,
            "tracks_synced": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        await self.firestore.set_document(
            self.SERVICES_COLLECTION,
            service_id,
            service_data,
        )

        return self._doc_to_music_service(service_data)

    async def create_lastfm_service(
        self,
        user_id: str,
        username: str,
    ) -> MusicService:
        """Create Last.fm service connection.

        Last.fm uses API key auth, not OAuth, so we just store the username.

        Args:
            user_id: User ID.
            username: Last.fm username.

        Returns:
            Created MusicService.

        Raises:
            ValidationError: If username is invalid or doesn't exist.
        """
        # Validate username by fetching user info
        try:
            user_info = await self.lastfm.get_user_info(username)
            # Extract user data from response
            user_data = user_info.get("user", {})
            lastfm_username = user_data.get("name", username)
        except ExternalServiceError as e:
            raise ValidationError(f"Invalid Last.fm username: {e}")

        service_id = self._get_service_id(user_id, "lastfm")
        now = datetime.now(UTC)

        # Check if already exists
        existing = await self.get_service(user_id, "lastfm")
        if existing:
            # Update existing
            await self.firestore.update_document(
                self.SERVICES_COLLECTION,
                service_id,
                {
                    "service_username": lastfm_username,
                    "updated_at": now.isoformat(),
                },
            )
            return MusicService(
                id=service_id,
                user_id=user_id,
                service_type="lastfm",
                service_user_id=lastfm_username,
                service_username=lastfm_username,
                access_token=None,
                refresh_token=None,
                token_expires_at=None,
                last_sync_at=existing.last_sync_at,
                sync_status=existing.sync_status,
                sync_error=existing.sync_error,
                tracks_synced=existing.tracks_synced,
                created_at=existing.created_at,
                updated_at=now,
            )

        # Create new service
        service_data = {
            "id": service_id,
            "user_id": user_id,
            "service_type": "lastfm",
            "service_user_id": lastfm_username,
            "service_username": lastfm_username,
            "access_token": None,
            "refresh_token": None,
            "token_expires_at": None,
            "last_sync_at": None,
            "sync_status": "idle",
            "sync_error": None,
            "tracks_synced": 0,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        await self.firestore.set_document(
            self.SERVICES_COLLECTION,
            service_id,
            service_data,
        )

        return self._doc_to_music_service(service_data)

    async def delete_service(self, user_id: str, service_type: str) -> None:
        """Disconnect a music service.

        Args:
            user_id: User ID.
            service_type: Service type to disconnect.

        Raises:
            NotFoundError: If service connection doesn't exist.
        """
        service_id = self._get_service_id(user_id, service_type)
        existing = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)

        if existing is None:
            raise NotFoundError(f"Service {service_type} not connected")

        await self.firestore.delete_document(self.SERVICES_COLLECTION, service_id)

    async def update_sync_status(
        self,
        user_id: str,
        service_type: str,
        status: str,
        error: str | None = None,
        tracks_synced: int | None = None,
        songs_synced: int | None = None,
    ) -> None:
        """Update sync status for a service.

        Args:
            user_id: User ID.
            service_type: Service type.
            status: New sync status (idle, syncing, error).
            error: Error message if status is error.
            tracks_synced: Karaoke-matched track count if sync completed.
            songs_synced: Total unique songs synced (all tracks).
        """
        service_id = self._get_service_id(user_id, service_type)
        now = datetime.now(UTC)

        update_data: dict[str, Any] = {
            "sync_status": status,
            "updated_at": now.isoformat(),
        }

        if error is not None:
            update_data["sync_error"] = error
        elif status != "error":
            update_data["sync_error"] = None

        if status == "idle" and tracks_synced is not None:
            update_data["tracks_synced"] = tracks_synced
            update_data["last_sync_at"] = now.isoformat()

        if status == "idle" and songs_synced is not None:
            update_data["songs_synced"] = songs_synced

        await self.firestore.update_document(
            self.SERVICES_COLLECTION,
            service_id,
            update_data,
        )

    async def update_scrobble_progress(
        self,
        user_id: str,
        service_type: str,
        oldest_scrobble_timestamp: int | None = None,
        scrobble_history_complete: bool | None = None,
        scrobbles_processed: int | None = None,
    ) -> None:
        """Update scrobble sync progress for incremental sync.

        Tracks progress through scrobble history so sync can resume from
        where it left off if interrupted.

        Args:
            user_id: User ID.
            service_type: Service type (should be 'lastfm').
            oldest_scrobble_timestamp: Unix timestamp of oldest scrobble processed.
            scrobble_history_complete: True if all history has been synced.
            scrobbles_processed: Total number of scrobbles processed so far.
        """
        service_id = self._get_service_id(user_id, service_type)
        now = datetime.now(UTC)

        update_data: dict[str, Any] = {
            "updated_at": now.isoformat(),
        }

        if oldest_scrobble_timestamp is not None:
            update_data["oldest_scrobble_timestamp"] = oldest_scrobble_timestamp

        if scrobble_history_complete is not None:
            update_data["scrobble_history_complete"] = scrobble_history_complete

        if scrobbles_processed is not None:
            update_data["scrobbles_processed"] = scrobbles_processed

        await self.firestore.update_document(
            self.SERVICES_COLLECTION,
            service_id,
            update_data,
        )

    async def get_scrobble_progress(
        self,
        user_id: str,
        service_type: str,
    ) -> dict[str, Any]:
        """Get scrobble sync progress for a service.

        Returns:
            Dict with oldest_scrobble_timestamp, scrobble_history_complete,
            and scrobbles_processed fields.
        """
        service_id = self._get_service_id(user_id, service_type)
        doc = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)

        if not doc:
            return {
                "oldest_scrobble_timestamp": None,
                "scrobble_history_complete": False,
                "scrobbles_processed": 0,
            }

        return {
            "oldest_scrobble_timestamp": doc.get("oldest_scrobble_timestamp"),
            "scrobble_history_complete": doc.get("scrobble_history_complete", False),
            "scrobbles_processed": doc.get("scrobbles_processed", 0),
        }

    # -------------------------------------------------------------------------
    # Token Management
    # -------------------------------------------------------------------------

    async def refresh_spotify_token_if_needed(self, service: MusicService) -> MusicService:
        """Refresh Spotify token if expired or expiring soon.

        Args:
            service: MusicService to check/refresh.

        Returns:
            MusicService with fresh token.

        Raises:
            MusicServiceError: If token refresh fails.
        """
        if service.service_type != "spotify":
            return service

        if not service.refresh_token:
            raise MusicServiceError("No refresh token available")

        # Check if token needs refresh
        now = datetime.now(UTC)
        if service.token_expires_at:
            buffer = timedelta(minutes=self.TOKEN_REFRESH_BUFFER_MINUTES)
            if service.token_expires_at > (now + buffer):
                # Token still valid
                return service

        # Refresh token
        try:
            tokens = await self.spotify.refresh_token(service.refresh_token)
        except ExternalServiceError as e:
            raise MusicServiceError(f"Failed to refresh Spotify token: {e}")

        # Update service with new tokens
        service_id = self._get_service_id(service.user_id, "spotify")
        expires_in = tokens.get("expires_in", 3600)
        token_expires_at = now + timedelta(seconds=expires_in)

        update_data: dict[str, Any] = {
            "access_token": tokens.get("access_token"),
            "token_expires_at": token_expires_at.isoformat(),
            "updated_at": now.isoformat(),
        }

        # Spotify may return a new refresh token
        if tokens.get("refresh_token"):
            update_data["refresh_token"] = tokens["refresh_token"]

        await self.firestore.update_document(
            self.SERVICES_COLLECTION,
            service_id,
            update_data,
        )

        return MusicService(
            id=service.id,
            user_id=service.user_id,
            service_type=service.service_type,
            service_user_id=service.service_user_id,
            service_username=service.service_username,
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token") or service.refresh_token,
            token_expires_at=token_expires_at,
            last_sync_at=service.last_sync_at,
            sync_status=service.sync_status,
            sync_error=service.sync_error,
            tracks_synced=service.tracks_synced,
            created_at=service.created_at,
            updated_at=now,
        )

    async def get_valid_spotify_token(self, service: MusicService) -> str:
        """Get a valid Spotify access token, refreshing if necessary.

        Args:
            service: Spotify MusicService.

        Returns:
            Valid access token string.

        Raises:
            MusicServiceError: If unable to get valid token.
        """
        refreshed = await self.refresh_spotify_token_if_needed(service)
        if not refreshed.access_token:
            raise MusicServiceError("No access token available")
        return refreshed.access_token

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _doc_to_music_service(self, doc: dict[str, Any]) -> MusicService:
        """Convert Firestore document to MusicService model.

        Args:
            doc: Firestore document dict.

        Returns:
            MusicService model.
        """
        return MusicService(
            id=doc.get("id", ""),
            user_id=doc.get("user_id", ""),
            service_type=doc.get("service_type", "spotify"),
            service_user_id=doc.get("service_user_id", ""),
            service_username=doc.get("service_username", ""),
            access_token=doc.get("access_token"),
            refresh_token=doc.get("refresh_token"),
            token_expires_at=(datetime.fromisoformat(doc["token_expires_at"]) if doc.get("token_expires_at") else None),
            last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
            sync_status=doc.get("sync_status", "idle"),
            sync_error=doc.get("sync_error"),
            tracks_synced=doc.get("tracks_synced", 0),
            songs_synced=doc.get("songs_synced", 0),
            created_at=(datetime.fromisoformat(doc["created_at"]) if doc.get("created_at") else datetime.now(UTC)),
            updated_at=(datetime.fromisoformat(doc["updated_at"]) if doc.get("updated_at") else datetime.now(UTC)),
        )

    def get_spotify_auth_url(self, state: str) -> str:
        """Get Spotify OAuth authorization URL.

        Args:
            state: OAuth state token for CSRF protection.

        Returns:
            Full authorization URL to redirect user to.
        """
        return self.spotify.get_auth_url(state)


# Lazy initialization
_music_service_service: MusicServiceService | None = None


def get_music_service_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> MusicServiceService:
    """Get the music service service instance.

    Args:
        settings: Optional settings override (for testing).
        firestore: Optional Firestore service override (for testing).

    Returns:
        MusicServiceService instance.
    """
    global _music_service_service

    if _music_service_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)
        _music_service_service = MusicServiceService(settings, firestore)

    return _music_service_service
