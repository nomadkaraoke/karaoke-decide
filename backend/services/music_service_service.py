"""Music service connection management.

Handles OAuth flows, token management, and CRUD operations for
connected music services (Spotify, Last.fm).
"""

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import IMPORTS_COLLECTION
from karaoke_decide.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from karaoke_decide.core.models import MusicService
from karaoke_decide.services.apple_music import AppleMusicClient, DeveloperTokenSigner
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.listenbrainz import ListenBrainzClient
from karaoke_decide.services.metabrainz_oauth import MetaBrainzInvalidGrantError, MetaBrainzOAuth
from karaoke_decide.services.spotify import SpotifyClient, SpotifyInvalidGrantError
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

if TYPE_CHECKING:
    from backend.services.token_cipher import TokenCipher

logger = logging.getLogger(__name__)


class _LeaseHeldError(Exception):
    """Aborts a lease transaction without writing."""


class MusicServiceError(Exception):
    """Error during music service operations."""

    pass


class ListenBrainzTokenUnavailableError(MusicServiceError):
    """The ListenBrainz token can't be had right now (refresh busy or MetaBrainz down); retry later.

    Unlike its parent, the connection itself is fine: callers should answer 503, not needs_reconnect.
    """


# Strong references to in-flight shielded token refreshes, so a cancelled caller can't let the
# task be garbage-collected mid-refresh (asyncio keeps only weak references to tasks).
_background_refreshes: set[asyncio.Task[str]] = set()


def _forget_refresh(task: asyncio.Task[str]) -> None:
    _background_refreshes.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved: no "exception was never retrieved" noise; callers already saw it


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
        listenbrainz_client: ListenBrainzClient | None = None,
        apple_music_client: AppleMusicClient | None = None,
        metabrainz_oauth: MetaBrainzOAuth | None = None,
    ):
        """Initialize the music service service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for persistence.
            spotify_client: Optional Spotify client (created lazily if not provided).
            lastfm_client: Optional Last.fm client (created lazily if not provided).
            listenbrainz_client: Optional ListenBrainz client (created lazily if not provided).
        """
        self.settings = settings
        self.firestore = firestore
        self._spotify_client = spotify_client
        self._lastfm_client = lastfm_client
        self._listenbrainz_client = listenbrainz_client
        self._apple_music_signer: DeveloperTokenSigner | None = None
        self._apple_music_client = apple_music_client
        self._metabrainz_oauth = metabrainz_oauth

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

    @property
    def listenbrainz(self) -> ListenBrainzClient:
        """Get or create ListenBrainz client."""
        if self._listenbrainz_client is None:
            self._listenbrainz_client = ListenBrainzClient(self.settings)
        return self._listenbrainz_client

    @property
    def apple_music_signer(self) -> DeveloperTokenSigner:
        """Get or create the Apple Music developer token signer (caches signed tokens)."""
        if self._apple_music_signer is None:
            self._apple_music_signer = DeveloperTokenSigner(self.settings)
        return self._apple_music_signer

    @property
    def _cipher(self) -> "TokenCipher":
        """Tokens are encrypted on every write and decrypted in _doc_to_music_service; with no
        DECIDE_TOKEN_KEY the cipher is the identity, and legacy plaintext rows read as-is.
        Imported lazily: ``cryptography`` stays off the cold-start import path."""
        from backend.services.token_cipher import get_token_cipher

        return get_token_cipher(self.settings)

    @property
    def metabrainz(self) -> MetaBrainzOAuth:
        """Get or create the MetaBrainz OAuth client (ListenBrainz connect)."""
        if self._metabrainz_oauth is None:
            self._metabrainz_oauth = MetaBrainzOAuth(self.settings)
        return self._metabrainz_oauth

    @property
    def apple_music(self) -> AppleMusicClient:
        """Get or create Apple Music client."""
        if self._apple_music_client is None:
            self._apple_music_client = AppleMusicClient(self.apple_music_signer)
        return self._apple_music_client

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

    async def create_oauth_state(
        self,
        user_id: str,
        service_type: str,
        account_hint: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> str:
        """Create and store OAuth state for CSRF protection.

        Args:
            user_id: User initiating OAuth flow.
            service_type: Service type (e.g., "spotify").
            account_hint: Masked email shown to whoever opens a hand-off link (Apple Music).
            extra: Flow data returned by verify_oauth_state (e.g. PKCE verifier, return target).

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
                "account_hint": account_hint,
                "extra": dict(extra or {}),
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
        )

        return state

    async def peek_oauth_state(self, state: str) -> dict[str, str | None] | None:
        """Read a valid, unexpired state without consuming it (user_id, service_type, account_hint)."""
        doc = await self.firestore.get_document(self.OAUTH_STATES_COLLECTION, state)
        if doc is None or datetime.now(UTC) > datetime.fromisoformat(doc["expires_at"]):
            return None
        return {
            "user_id": doc["user_id"],
            "service_type": doc["service_type"],
            "account_hint": doc.get("account_hint"),
        }

    async def verify_oauth_state(self, state: str) -> dict[str, str] | None:
        """Verify and consume OAuth state atomically.

        Uses an atomic delete-and-return to prevent TOCTOU race conditions
        where two concurrent callbacks could both pass verification.

        Args:
            state: State token from OAuth callback.

        Returns:
            Dict with user_id, service_type and the state's ``extra`` fields if valid, else None.
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

        extra = doc.get("extra") or {}
        return {
            **{str(k): str(v) for k, v in extra.items()},
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

    async def get_source_summaries(self, user_id: str) -> list[dict[str, Any]]:
        """One row per source the user has: every ``music_services`` connection merged with its
        ``user_imports`` doc (if any), plus import-only sources. Counts are Firestore aggregation
        queries on ``user_artists`` / ``user_songs`` by each row's primary ``source``."""
        conns = await self.firestore.query_documents(self.SERVICES_COLLECTION, filters=[("user_id", "==", user_id)])
        return await self._summaries(user_id, conns)

    async def count_known_songs(self, user_id: str) -> int:
        """The user's distinct songs the recommender treats as known: rows deduped by ``song_key``
        (a song can sit on both a ``sk:`` and a catalog-id row), counted when loved or when the best
        of its sources has a positive weight. Voice-session-only unloved rows weigh 0."""
        from backend.services.recs_v2.context import _base_weight
        from backend.services.song_feedback_service import is_loved

        docs = await self.firestore.query_documents("user_songs", filters=[("user_id", "==", user_id)])
        keys: set[str] = set()
        empty = "u:" + make_song_key("", "")
        for d in docs:
            artist, title = d.get("artist") or "", d.get("title") or ""
            if not d.get("song_key") and not (artist and title):
                continue  # no identity: not a song
            key = d.get("song_key") or ("u:" + make_song_key(artist, title))
            if key != empty and (is_loved(d) or _base_weight(d) > 0):
                keys.add(str(key))
        return len(keys)

    async def get_services_with_summaries(self, user_id: str) -> tuple[list[MusicService], list[dict[str, Any]]]:
        """``get_user_services`` plus ``get_source_summaries`` from one ``music_services`` query."""
        conns = await self.firestore.query_documents(self.SERVICES_COLLECTION, filters=[("user_id", "==", user_id)])
        return [self._doc_to_music_service(c) for c in conns], await self._summaries(user_id, conns)

    async def _summaries(self, user_id: str, conns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        imports = await self.firestore.query_documents(IMPORTS_COLLECTION, filters=[("user_id", "==", user_id)])
        conn_by = {str(c["service_type"]): c for c in conns if c.get("service_type")}
        imp_by = {str(i["source"]): i for i in imports if i.get("source")}
        sources = list(conn_by) + [s for s in imp_by if s not in conn_by]

        async def count(collection: str, source: str) -> int:
            return await self.firestore.count_documents(
                collection, filters=[("user_id", "==", user_id), ("source", "==", source)]
            )

        counts = await asyncio.gather(
            *(asyncio.gather(count("user_artists", s), count("user_songs", s)) for s in sources)
        )
        return [
            self._summary_row(s, conn_by.get(s), imp_by.get(s), artists, songs)
            for s, (artists, songs) in zip(sources, counts, strict=True)
        ]

    @staticmethod
    def _summary_row(
        source: str, conn: dict[str, Any] | None, imp: dict[str, Any] | None, artists: int, songs: int
    ) -> dict[str, Any]:
        conn, imp = conn or {}, imp or {}

        def field(name: str) -> Any:
            return conn.get(name) if conn.get(name) is not None else imp.get(name)

        if conn.get("sync_status") == "syncing" or imp.get("status") == "importing":
            status = "importing"
        elif field("error_code") == "needs_reconnect":
            status = "needs_reconnect"
        elif field("lb_stale"):
            status = "stale"
        elif conn.get("sync_status") == "error" or imp.get("status") == "error":
            status = "error"
        else:
            status = "ok"
        auth = field("auth")
        if auth not in ("username", "oauth"):
            auth = {"spotify": "oauth", "apple_music": "oauth", "lastfm": "username", "listenbrainz": "username"}.get(
                source
            )
        pending = field("lb_pending_import") or field("pending_listenbrainz_import")
        return {
            "source": source,
            "connected": bool(conn),
            "username": conn.get("service_username") or None,
            "artists": artists,
            "songs": songs,
            "last_import_at": imp.get("last_import_at") or conn.get("last_sync_at"),
            "status": status,
            "auth": auth if conn else None,
            "pending_listenbrainz_import": (
                {"id": str(pending["id"]), "status": str(pending.get("status", ""))}
                if isinstance(pending, dict) and pending.get("id")
                else None
            ),
        }

    async def get_service_doc(self, user_id: str, service_type: str) -> dict[str, Any] | None:
        """The raw stored connection document (tokens still encrypted), or None."""
        return await self.firestore.get_document(self.SERVICES_COLLECTION, self._get_service_id(user_id, service_type))

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
                "access_token": self._cipher.encrypt(tokens.get("access_token")),
                "token_expires_at": token_expires_at.isoformat(),
                "updated_at": now.isoformat(),
                # A reconnect clears a needs_reconnect flag left by a dead refresh token
                "error_code": None,
            }
            if existing.sync_status == "error":
                update_data["sync_status"] = "idle"
                update_data["sync_error"] = None

            # Only update refresh_token if provided (Spotify doesn't always return it)
            if tokens.get("refresh_token"):
                update_data["refresh_token"] = self._cipher.encrypt(tokens["refresh_token"])

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
            "access_token": self._cipher.encrypt(tokens.get("access_token")),
            "refresh_token": self._cipher.encrypt(tokens.get("refresh_token")),
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

    async def create_listenbrainz_service(
        self,
        user_id: str,
        username: str,
    ) -> MusicService:
        """Create ListenBrainz service connection.

        ListenBrainz is an open-source alternative to Last.fm that can import
        data from Spotify, Apple Music, and other services. No API key needed.

        Args:
            user_id: User ID.
            username: ListenBrainz username.

        Returns:
            Created MusicService.

        Raises:
            ValidationError: If username is invalid or doesn't exist.
        """
        # Validate username by fetching listen count
        try:
            user_info = await self.listenbrainz.get_user_info(username)
            lb_username = user_info.username
        except ExternalServiceError as e:
            raise ValidationError(f"Invalid ListenBrainz username: {e}")

        service_id = self._get_service_id(user_id, "listenbrainz")
        now = datetime.now(UTC)

        # Check if already exists
        existing = await self.get_service(user_id, "listenbrainz")
        if existing:
            # Update existing
            await self.firestore.update_document(
                self.SERVICES_COLLECTION,
                service_id,
                {
                    "service_username": lb_username,
                    "service_user_id": lb_username,
                    # A username connection replaces an OAuth one.
                    "auth": "username",
                    "access_token": None,
                    "refresh_token": None,
                    "token_expires_at": None,
                    "error_code": None,
                    "updated_at": now.isoformat(),
                    **self._lb_poll_reset(existing.service_username, lb_username),
                },
            )
            return MusicService(
                id=service_id,
                user_id=user_id,
                service_type="listenbrainz",
                service_user_id=lb_username,
                service_username=lb_username,
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
            "service_type": "listenbrainz",
            "service_user_id": lb_username,
            "service_username": lb_username,
            "auth": "username",
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

    _LB_POLL_FIELDS = ("lb_last_listen_ts", "lb_last_listen_at", "lb_last_polled_at", "lb_stale", "lb_pending_import")

    @classmethod
    def _lb_poll_reset(cls, old_username: str | None, new_username: str) -> dict[str, Any]:
        """Poller state to clear when the connected ListenBrainz account changes (so the next poll backfills)."""
        if old_username and old_username.casefold() != new_username.casefold():
            return dict.fromkeys(cls._LB_POLL_FIELDS)
        return {}

    async def create_listenbrainz_oauth_service(
        self, user_id: str, tokens: dict[str, Any], username: str
    ) -> MusicService:
        """Store a ListenBrainz connection authorised through MetaBrainz OAuth.

        Tokens are encrypted at rest. Reconnecting replaces the tokens, clears a
        ``needs_reconnect`` error and any refresh lease, and keeps the sync history (an
        in-progress ``syncing`` status is left alone).
        """
        service_id = self._get_service_id(user_id, "listenbrainz")
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=int(tokens.get("expires_in") or 3600))
        fields: dict[str, Any] = {
            "service_user_id": username,
            "service_username": username,
            "auth": "oauth",
            "access_token": self._cipher.encrypt(tokens.get("access_token")),
            "refresh_token": self._cipher.encrypt(tokens.get("refresh_token")),
            "token_expires_at": expires_at.isoformat(),
            "sync_status": "idle",
            "sync_error": None,
            "error_code": None,
            "refresh_lease_until": None,
            "updated_at": now.isoformat(),
        }

        existing = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)
        if existing:
            if existing.get("sync_status") == "syncing":
                del fields["sync_status"]  # a sync in flight owns its status
            fields.update(self._lb_poll_reset(existing.get("service_username"), username))
            await self.firestore.update_document(self.SERVICES_COLLECTION, service_id, fields)
            return self._doc_to_music_service({**existing, **fields})

        service_data: dict[str, Any] = {
            "id": service_id,
            "user_id": user_id,
            "service_type": "listenbrainz",
            "last_sync_at": None,
            "tracks_synced": 0,
            "created_at": now.isoformat(),
            **fields,
        }
        await self.firestore.set_document(self.SERVICES_COLLECTION, service_id, service_data)
        return self._doc_to_music_service(service_data)

    async def create_apple_music_service(self, user_id: str, music_user_token: str) -> MusicService:
        """Connect Apple Music with the Music User Token MusicKit JS returned.

        The token is checked against Apple first (storefront lookup), then stored as the
        service's access token. Reconnecting replaces the token and keeps the sync history.

        Raises:
            ValidationError: Apple refused the token.
        """
        try:
            storefront = await self.apple_music.get_storefront(music_user_token)
        except ExternalServiceError as e:
            raise ValidationError(f"Apple Music didn't accept the sign-in: {e}")

        service_id = self._get_service_id(user_id, "apple_music")
        now = datetime.now(UTC)
        fields: dict[str, Any] = {
            "service_user_id": storefront,
            "service_username": "Apple Music",
            "access_token": self._cipher.encrypt(music_user_token),
            "sync_status": "idle",
            "sync_error": None,
            "updated_at": now.isoformat(),
        }

        existing = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)
        if existing:
            await self.firestore.update_document(self.SERVICES_COLLECTION, service_id, fields)
            return self._doc_to_music_service({**existing, **fields})

        service_data: dict[str, Any] = {
            "id": service_id,
            "user_id": user_id,
            "service_type": "apple_music",
            "refresh_token": None,
            "token_expires_at": None,
            "last_sync_at": None,
            "tracks_synced": 0,
            "created_at": now.isoformat(),
            **fields,
        }
        await self.firestore.set_document(self.SERVICES_COLLECTION, service_id, service_data)
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
        except SpotifyInvalidGrantError:
            service_id = self._get_service_id(service.user_id, "spotify")
            logger.warning("Spotify refresh token for %s was revoked or expired; needs reconnect", service_id)
            await self._mark_needs_reconnect(
                service_id, "Spotify access expired. Reconnect Spotify.", service.refresh_token
            )
            raise MusicServiceError("Spotify needs to be reconnected")
        except ExternalServiceError as e:
            raise MusicServiceError(f"Failed to refresh Spotify token: {e}")

        # Update service with new tokens
        service_id = self._get_service_id(service.user_id, "spotify")
        expires_in = tokens.get("expires_in", 3600)
        token_expires_at = now + timedelta(seconds=expires_in)

        update_data: dict[str, Any] = {
            "access_token": self._cipher.encrypt(tokens.get("access_token")),
            "token_expires_at": token_expires_at.isoformat(),
            "updated_at": now.isoformat(),
        }

        # Spotify may return a new refresh token
        if tokens.get("refresh_token"):
            update_data["refresh_token"] = self._cipher.encrypt(tokens["refresh_token"])

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

    REFRESH_LEASE_SECONDS = 90
    REFRESH_CALL_TIMEOUT_SECONDS = 45.0
    # Waiters must outlast a holder's refresh call (REFRESH_CALL_TIMEOUT_SECONDS) plus its write.
    REFRESH_WAIT_SECONDS = 50.0
    REFRESH_POLL_SECONDS = 0.2

    @staticmethod
    def _lease_active(doc: dict[str, Any] | None, now: datetime) -> bool:
        """Whether the doc holds a live refresh lease (absent, past or malformed counts as none)."""
        held = (doc or {}).get("refresh_lease_until")
        if not held:
            return False
        try:
            return datetime.fromisoformat(held) > now
        except (TypeError, ValueError):
            return False

    async def _claim_refresh_lease(self, service_id: str) -> str | None:
        """Atomically take the per-user refresh lease; returns its token, or None if held.

        A held lease aborts the transaction without writing, so waiters never write.
        """
        now = datetime.now(UTC)
        lease = (now + timedelta(seconds=self.REFRESH_LEASE_SECONDS)).isoformat()

        def transform(doc: dict[str, Any] | None) -> dict[str, Any] | None:
            if doc is None:
                return None
            if self._lease_active(doc, now):
                raise _LeaseHeldError
            return {**doc, "refresh_lease_until": lease}

        try:
            written = await self.firestore.transform_document_atomically(
                self.SERVICES_COLLECTION, service_id, transform
            )
        except _LeaseHeldError:
            return None
        return lease if written is not None else None

    async def _release_refresh_lease(self, service_id: str) -> None:
        try:
            await self.firestore.update_document(self.SERVICES_COLLECTION, service_id, {"refresh_lease_until": None})
        except Exception:
            logger.warning("Could not release ListenBrainz refresh lease %s", service_id, exc_info=True)

    _LB_RECONNECT_MESSAGE = "ListenBrainz access expired. Reconnect ListenBrainz."

    async def _mark_listenbrainz_needs_reconnect(self, service_id: str, used_refresh_token: str | None = None) -> bool:
        return await self._mark_needs_reconnect(service_id, self._LB_RECONNECT_MESSAGE, used_refresh_token)

    async def _mark_needs_reconnect(self, service_id: str, message: str, used_refresh_token: str | None = None) -> bool:
        """Mark the connection ``needs_reconnect``; returns whether it was marked.

        With ``used_refresh_token``, only if the stored refresh token is still that one: a
        reconnect that landed meanwhile stored fresh tokens, which must not be flagged dead.
        """
        fields = {
            "sync_status": "error",
            "sync_error": message,
            "error_code": "needs_reconnect",
            "refresh_lease_until": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if used_refresh_token is None:
            await self.firestore.update_document(self.SERVICES_COLLECTION, service_id, fields)
            return True

        def mark(doc: dict[str, Any] | None) -> dict[str, Any] | None:
            if doc is None or self._decrypt(doc.get("refresh_token"), doc) != used_refresh_token:
                raise _LeaseHeldError  # reconnected (or disconnected) meanwhile: leave it alone
            return {**doc, **fields}

        try:
            await self.firestore.transform_document_atomically(self.SERVICES_COLLECTION, service_id, mark)
        except _LeaseHeldError:
            return False
        return True

    def _undecryptable_oauth_tokens(self, doc: dict[str, Any] | None) -> bool:
        """An OAuth ListenBrainz doc whose stored tokens this process's key can't decrypt.

        Only counts with a key configured: a process missing ``DECIDE_TOKEN_KEY`` is misconfigured,
        which says nothing about the connection itself.
        """
        if not doc or doc.get("auth") != "oauth" or not self._cipher.enabled:
            return False
        for field in ("access_token", "refresh_token"):
            value = doc.get(field)
            if not value:
                continue
            try:
                self._cipher.decrypt(value)
            except Exception:
                return True
        return False

    async def _bail_if_undecryptable(self, service_id: str, doc: dict[str, Any] | None) -> None:
        """A failed decrypt leaves no usable token: flag the connection rather than let it die silently."""
        if not self._undecryptable_oauth_tokens(doc):
            return
        logger.warning("ListenBrainz tokens for %s can't be decrypted (key rotated?); needs reconnect", service_id)
        await self._mark_listenbrainz_needs_reconnect(service_id)
        raise MusicServiceError("ListenBrainz needs to be reconnected")

    def _fresh_listenbrainz_token(self, doc: dict[str, Any] | None) -> str | None:
        if not doc:
            return None
        stored = self._doc_to_music_service(doc)
        buffer = timedelta(minutes=self.TOKEN_REFRESH_BUFFER_MINUTES)
        if stored.access_token and stored.token_expires_at and stored.token_expires_at > datetime.now(UTC) + buffer:
            return stored.access_token
        return None

    async def get_listenbrainz_token(self, service: MusicService) -> str:
        """A valid MetaBrainz OAuth access token for a ListenBrainz connection.

        Refreshes when the token expires within TOKEN_REFRESH_BUFFER_MINUTES and stores the
        rotated tokens. MetaBrainz rotates refresh tokens and revokes everything when an old
        one is reused, so refreshes are serialised per user by a Firestore lease
        (``refresh_lease_until``): one caller refreshes, the rest poll (read-only) for the stored
        token. The rotated tokens are written in a transaction that checks the lease is still ours;
        if that fails the connection is marked ``needs_reconnect`` (the new refresh token is lost).
        A dead refresh token also marks it ``sync_status="error"``, ``error_code="needs_reconnect"``.

        Stored OAuth tokens that no longer decrypt (key rotated) also mark ``needs_reconnect``.

        Raises:
            ListenBrainzTokenUnavailableError: Timed out waiting for another caller's refresh, or
                MetaBrainz failed transiently: retry later; the connection is fine.
            MusicServiceError: No OAuth token (username connection), or it needs reconnecting.
        """
        if service.service_type != "listenbrainz":
            raise MusicServiceError("ListenBrainz is not connected with OAuth")
        service_id = self._get_service_id(service.user_id, "listenbrainz")
        if not service.access_token or not service.refresh_token:
            await self._bail_if_undecryptable(
                service_id, await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)
            )
        if not service.access_token:
            raise MusicServiceError("ListenBrainz is not connected with OAuth")

        buffer = timedelta(minutes=self.TOKEN_REFRESH_BUFFER_MINUTES)
        if service.token_expires_at and service.token_expires_at > datetime.now(UTC) + buffer:
            return service.access_token
        if not service.refresh_token:
            raise MusicServiceError("No refresh token available")

        deadline = asyncio.get_running_loop().time() + self.REFRESH_WAIT_SECONDS
        while True:
            doc = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)
            fresh = self._fresh_listenbrainz_token(doc)
            if fresh:
                return fresh
            if doc is None or doc.get("error_code") == "needs_reconnect":
                raise MusicServiceError("ListenBrainz needs to be reconnected")
            if not self._lease_active(doc, datetime.now(UTC)):
                lease = await self._claim_refresh_lease(service_id)
                if lease:
                    return await self._refresh_listenbrainz_holding_lease(service, service_id, lease)
            if asyncio.get_running_loop().time() > deadline:
                raise ListenBrainzTokenUnavailableError("Timed out waiting for the ListenBrainz token refresh")
            await asyncio.sleep(self.REFRESH_POLL_SECONDS)

    async def _refresh_listenbrainz_holding_lease(self, service: MusicService, service_id: str, lease: str) -> str:
        try:
            # Re-read: another caller may have rotated the tokens since we last looked.
            doc = await self.firestore.get_document(self.SERVICES_COLLECTION, service_id)
            fresh = self._fresh_listenbrainz_token(doc)
            if fresh:
                await self._release_refresh_lease(service_id)
                return fresh
            if doc is None or doc.get("error_code") == "needs_reconnect":
                await self._release_refresh_lease(service_id)
                raise MusicServiceError("ListenBrainz needs to be reconnected")
            refresh_token = self._doc_to_music_service(doc).refresh_token
            if not refresh_token:
                await self._release_refresh_lease(service_id)
                await self._bail_if_undecryptable(service_id, doc)
                raise MusicServiceError("No refresh token available")
        except MusicServiceError:
            raise
        except BaseException:
            await self._release_refresh_lease(service_id)
            raise
        # Shielded: once MetaBrainz may have rotated the token, cancellation must not stop us
        # persisting it.
        task = asyncio.ensure_future(self._refresh_and_store(service.user_id, service_id, lease, refresh_token))
        _background_refreshes.add(task)
        task.add_done_callback(_forget_refresh)
        return await asyncio.shield(task)

    async def _refresh_and_store(self, user_id: str, service_id: str, lease: str, refresh_token: str) -> str:
        try:
            async with asyncio.timeout(self.REFRESH_CALL_TIMEOUT_SECONDS):
                tokens = await self.metabrainz.refresh(refresh_token)
        except MetaBrainzInvalidGrantError:
            if not await self._mark_listenbrainz_needs_reconnect(service_id, refresh_token):
                raise ListenBrainzTokenUnavailableError("ListenBrainz was reconnected meanwhile")
            logger.warning("ListenBrainz refresh token rejected for user %s; needs reconnect", user_id)
            raise MusicServiceError("ListenBrainz needs to be reconnected")
        except (ExternalServiceError, TimeoutError) as e:
            await self._release_refresh_lease(service_id)
            raise ListenBrainzTokenUnavailableError(f"Failed to refresh ListenBrainz token: {e}")
        except BaseException:
            await self._release_refresh_lease(service_id)
            raise

        now = datetime.now(UTC)
        access_token = str(tokens["access_token"])
        update_data: dict[str, Any] = {
            "access_token": self._cipher.encrypt(access_token),
            "token_expires_at": (now + timedelta(seconds=int(tokens.get("expires_in") or 3600))).isoformat(),
            "updated_at": now.isoformat(),
            "refresh_lease_until": None,
        }
        if tokens.get("refresh_token"):
            update_data["refresh_token"] = self._cipher.encrypt(tokens["refresh_token"])

        def store(doc: dict[str, Any] | None) -> dict[str, Any] | None:
            if doc is None or doc.get("refresh_lease_until") != lease:
                raise _LeaseHeldError  # lease expired or taken over: do not clobber
            return {**doc, **update_data}

        try:
            await self.firestore.transform_document_atomically(self.SERVICES_COLLECTION, service_id, store)
        except BaseException:
            try:
                marked = await self._mark_listenbrainz_needs_reconnect(service_id, refresh_token)
            except Exception:
                logger.error("Could not mark ListenBrainz needs_reconnect for user %s", user_id, exc_info=True)
                marked = True
            if not marked:
                # A reconnect replaced the tokens while we refreshed: its tokens are the live ones.
                raise ListenBrainzTokenUnavailableError("ListenBrainz was reconnected meanwhile")
            logger.error(
                "Could not store rotated ListenBrainz tokens for user %s; the old refresh token is revoked, "
                "user must reconnect",
                user_id,
                exc_info=True,
            )
            raise MusicServiceError("ListenBrainz needs to be reconnected")
        return access_token

    async def set_pending_listenbrainz_import(self, user_id: str, import_id: str) -> None:
        """Record a Spotify export handed to ListenBrainz (``lb_pending_import``)."""
        await self.firestore.update_document(
            self.SERVICES_COLLECTION,
            self._get_service_id(user_id, "listenbrainz"),
            {
                "lb_pending_import": {
                    "id": import_id,
                    "status": "waiting",
                    "submitted_at": datetime.now(UTC).isoformat(),
                }
            },
        )

    def service_from_doc(self, doc: dict[str, Any]) -> MusicService:
        """A stored connection document as a MusicService (tokens decrypted)."""
        return self._doc_to_music_service(doc)

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _decrypt(self, value: str | None, doc: dict[str, Any]) -> str | None:
        """Decrypt a stored token; an undecryptable one (key missing or rotated) reads as None."""
        try:
            return self._cipher.decrypt(value)
        except Exception:
            logger.warning(
                "Could not decrypt a %s token for %s (DECIDE_TOKEN_KEY missing or changed?)",
                doc.get("service_type"),
                doc.get("user_id"),
            )
            return None

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
            access_token=self._decrypt(doc.get("access_token"), doc),
            refresh_token=self._decrypt(doc.get("refresh_token"), doc),
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
