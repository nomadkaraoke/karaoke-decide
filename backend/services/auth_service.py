"""Authentication service for magic link auth and JWT management."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from backend.config import BackendSettings
from backend.services.email_service import EmailService, get_email_service
from backend.services.firestore_service import FirestoreService
from karaoke_decide.core.models import User


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class AuthService:
    """Service for authentication operations."""

    USERS_COLLECTION = "decide_users"
    MAGIC_LINKS_COLLECTION = "magic_links"

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        email_service: EmailService | None = None,
    ):
        self.settings = settings
        self.firestore = firestore
        self.email_service = email_service or get_email_service(settings)

    def generate_magic_link_token(self) -> str:
        """Generate a secure random token for magic links.

        Returns:
            64-character hex string (32 bytes of entropy)
        """
        return secrets.token_hex(32)

    def _hash_email(self, email: str) -> str:
        """Hash an email address for use as document ID.

        Args:
            email: Email address to hash

        Returns:
            SHA-256 hash of lowercase email
        """
        return hashlib.sha256(email.lower().encode()).hexdigest()

    def _generate_user_id(self, is_guest: bool = False) -> str:
        """Generate a unique user ID.

        Args:
            is_guest: If True, generates a guest_ prefixed ID

        Returns:
            user_ or guest_ prefixed random hex string
        """
        prefix = "guest" if is_guest else "user"
        return f"{prefix}_{secrets.token_hex(12)}"

    async def store_magic_link(self, email: str, token: str) -> None:
        """Store a magic link token in Firestore.

        Args:
            email: User's email address
            token: Magic link token
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.settings.magic_link_expiration_minutes)

        await self.firestore.set_document(
            self.MAGIC_LINKS_COLLECTION,
            token,  # Use token as document ID for easy lookup
            {
                "email": email.lower(),
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "used": False,
            },
        )

    async def verify_magic_link(self, token: str) -> str:
        """Verify a magic link token and return the associated email.

        Args:
            token: Magic link token to verify

        Returns:
            Email address associated with the token

        Raises:
            AuthenticationError: If token is invalid, expired, or already used
        """
        doc = await self.firestore.get_document(self.MAGIC_LINKS_COLLECTION, token)

        if doc is None:
            raise AuthenticationError("Invalid or expired token")

        if doc.get("used"):
            raise AuthenticationError("Token has already been used")

        expires_at = datetime.fromisoformat(doc["expires_at"])
        if datetime.now(UTC) > expires_at:
            raise AuthenticationError("Token has expired")

        # Mark token as used
        await self.firestore.update_document(
            self.MAGIC_LINKS_COLLECTION,
            token,
            {"used": True},
        )

        email: str = doc["email"]
        return email

    async def get_or_create_user(self, email: str) -> User:
        """Get an existing user or create a new one.

        Args:
            email: User's email address

        Returns:
            User model
        """
        email_hash = self._hash_email(email)
        doc = await self.firestore.get_document(self.USERS_COLLECTION, email_hash)

        if doc is not None:
            # Existing user
            return User(
                id=doc["user_id"],
                email=doc["email"],
                display_name=doc.get("display_name"),
                is_admin=doc.get("is_admin", False),
                created_at=datetime.fromisoformat(doc["created_at"]),
                updated_at=datetime.fromisoformat(doc["updated_at"]),
                total_songs_known=doc.get("total_songs_known", 0),
                total_songs_sung=doc.get("total_songs_sung", 0),
                last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
            )

        # Create new user
        now = datetime.now(UTC)
        user_id = self._generate_user_id()

        user_data = {
            "user_id": user_id,
            "email": email.lower(),
            "display_name": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "total_songs_known": 0,
            "total_songs_sung": 0,
            "last_sync_at": None,
        }

        await self.firestore.set_document(self.USERS_COLLECTION, email_hash, user_data)

        return User(
            id=user_id,
            email=email.lower(),
            display_name=None,
            created_at=now,
            updated_at=now,
            total_songs_known=0,
            total_songs_sung=0,
            last_sync_at=None,
        )

    async def get_user_by_id(self, user_id: str) -> User | None:
        """Get a user by their user ID.

        Args:
            user_id: User's ID (user_xxx or guest_xxx format)

        Returns:
            User model or None if not found
        """
        # Guest users are stored with user_id as document ID
        if user_id.startswith("guest_"):
            doc = await self.firestore.get_document(self.USERS_COLLECTION, user_id)
            if doc:
                return User(
                    id=doc["user_id"],
                    email=doc.get("email"),
                    display_name=doc.get("display_name"),
                    is_guest=doc.get("is_guest", False),
                    is_admin=doc.get("is_admin", False),
                    created_at=datetime.fromisoformat(doc["created_at"]),
                    updated_at=datetime.fromisoformat(doc["updated_at"]),
                    total_songs_known=doc.get("total_songs_known", 0),
                    total_songs_sung=doc.get("total_songs_sung", 0),
                    last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
                )

        # Regular users: query by user_id field
        docs = await self.firestore.query_documents(
            self.USERS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1,
        )

        if not docs:
            return None

        doc = docs[0]
        return User(
            id=doc["user_id"],
            email=doc.get("email"),
            display_name=doc.get("display_name"),
            is_guest=doc.get("is_guest", False),
            is_admin=doc.get("is_admin", False),
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
            total_songs_known=doc.get("total_songs_known", 0),
            total_songs_sung=doc.get("total_songs_sung", 0),
            last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
        )

    def generate_jwt(self, user: User) -> tuple[str, int]:
        """Generate a JWT token for the user.

        Args:
            user: User to generate token for

        Returns:
            Tuple of (token, expires_in_seconds)

        Raises:
            ValueError: If JWT secret is not configured
        """
        if not self.settings.jwt_secret:
            raise ValueError("JWT_SECRET is not configured")

        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self.settings.jwt_expiration_hours)
        expires_in = int((expires_at - now).total_seconds())

        payload = {
            "sub": user.id,
            "email": user.email,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        token = jwt.encode(
            payload,
            self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm,
        )

        return token, expires_in

    def validate_jwt(self, token: str) -> dict[str, Any]:
        """Validate a JWT token and return its claims.

        Args:
            token: JWT token to validate

        Returns:
            Token claims (sub, email, iat, exp)

        Raises:
            AuthenticationError: If token is invalid or expired
        """
        if not self.settings.jwt_secret:
            raise AuthenticationError("JWT_SECRET is not configured")

        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_secret,
                algorithms=[self.settings.jwt_algorithm],
            )
            return dict(payload)
        except JWTError as e:
            raise AuthenticationError(f"Invalid token: {e}")

    async def send_magic_link(self, email: str) -> bool:
        """Generate and send a magic link to the user's email.

        Args:
            email: User's email address

        Returns:
            True if magic link was sent successfully
        """
        token = self.generate_magic_link_token()
        await self.store_magic_link(email, token)
        return await self.email_service.send_magic_link(email, token)

    async def create_guest_user(self) -> User:
        """Create a new guest/anonymous user.

        Guest users can use the quiz and get recommendations but cannot
        connect music services until they verify their email.

        Returns:
            User model for the guest user
        """
        now = datetime.now(UTC)
        user_id = self._generate_user_id(is_guest=True)

        user_data = {
            "user_id": user_id,
            "email": None,
            "display_name": None,
            "is_guest": True,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "total_songs_known": 0,
            "total_songs_sung": 0,
            "last_sync_at": None,
        }

        # Use the user_id as document ID for guest users (no email to hash)
        await self.firestore.set_document(self.USERS_COLLECTION, user_id, user_data)

        return User(
            id=user_id,
            email=None,
            display_name=None,
            is_guest=True,
            created_at=now,
            updated_at=now,
            total_songs_known=0,
            total_songs_sung=0,
            last_sync_at=None,
        )

    def generate_guest_jwt(self, user: User) -> tuple[str, int]:
        """Generate a JWT token for a guest user.

        Guest tokens have longer expiry (30 days) to persist their session.

        Args:
            user: Guest user to generate token for

        Returns:
            Tuple of (token, expires_in_seconds)

        Raises:
            ValueError: If JWT secret is not configured
        """
        if not self.settings.jwt_secret:
            raise ValueError("JWT_SECRET is not configured")

        now = datetime.now(UTC)
        # Guest tokens last 30 days (vs 7 days for verified users)
        expires_at = now + timedelta(days=30)
        expires_in = int((expires_at - now).total_seconds())

        payload = {
            "sub": user.id,
            "email": None,
            "is_guest": True,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        token = jwt.encode(
            payload,
            self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm,
        )

        return token, expires_in

    async def upgrade_guest_to_verified(
        self,
        guest_user_id: str,
        email: str,
    ) -> User | None:
        """Upgrade a guest user to a verified user after email verification.

        This migrates all guest data (quiz results, etc.) to the verified account.
        If the email already has an account, the guest data is merged into it.

        Args:
            guest_user_id: The guest user's ID
            email: Verified email address

        Returns:
            The upgraded/merged User, or None if guest not found
        """
        # Get the guest user document
        guest_doc = await self.firestore.get_document(self.USERS_COLLECTION, guest_user_id)
        if guest_doc is None:
            return None

        if not guest_doc.get("is_guest", False):
            # Not a guest user, nothing to upgrade
            return await self.get_user_by_id(guest_user_id)

        email_hash = self._hash_email(email)
        existing_user_doc = await self.firestore.get_document(self.USERS_COLLECTION, email_hash)

        now = datetime.now(UTC)

        if existing_user_doc is not None:
            # Merge guest data into existing account
            merged_data = {
                "updated_at": now.isoformat(),
                "total_songs_known": existing_user_doc.get("total_songs_known", 0)
                + guest_doc.get("total_songs_known", 0),
            }
            # If guest completed quiz but existing user hasn't, keep guest quiz data
            if guest_doc.get("quiz_completed_at") and not existing_user_doc.get("quiz_completed_at"):
                merged_data["quiz_completed_at"] = guest_doc.get("quiz_completed_at")
                merged_data["quiz_songs_known"] = guest_doc.get("quiz_songs_known", [])
                merged_data["quiz_decade_pref"] = guest_doc.get("quiz_decade_pref")
                merged_data["quiz_energy_pref"] = guest_doc.get("quiz_energy_pref")

            await self.firestore.update_document(self.USERS_COLLECTION, email_hash, merged_data)

            # Migrate any UserSong documents from guest to existing user
            await self._migrate_user_songs(guest_user_id, existing_user_doc["user_id"])

            # Delete the guest user document
            await self.firestore.delete_document(self.USERS_COLLECTION, guest_user_id)

            return User(
                id=existing_user_doc["user_id"],
                email=email.lower(),
                display_name=existing_user_doc.get("display_name"),
                is_guest=False,
                created_at=datetime.fromisoformat(existing_user_doc["created_at"]),
                updated_at=now,
                total_songs_known=merged_data["total_songs_known"],
                total_songs_sung=existing_user_doc.get("total_songs_sung", 0),
                last_sync_at=(
                    datetime.fromisoformat(existing_user_doc["last_sync_at"])
                    if existing_user_doc.get("last_sync_at")
                    else None
                ),
            )
        else:
            # Create new verified user from guest data
            new_user_id = self._generate_user_id(is_guest=False)

            user_data = {
                "user_id": new_user_id,
                "email": email.lower(),
                "display_name": guest_doc.get("display_name"),
                "is_guest": False,
                "created_at": guest_doc.get("created_at", now.isoformat()),
                "updated_at": now.isoformat(),
                "total_songs_known": guest_doc.get("total_songs_known", 0),
                "total_songs_sung": guest_doc.get("total_songs_sung", 0),
                "last_sync_at": guest_doc.get("last_sync_at"),
                # Preserve quiz data
                "quiz_completed_at": guest_doc.get("quiz_completed_at"),
                "quiz_songs_known": guest_doc.get("quiz_songs_known", []),
                "quiz_decade_pref": guest_doc.get("quiz_decade_pref"),
                "quiz_energy_pref": guest_doc.get("quiz_energy_pref"),
            }

            await self.firestore.set_document(self.USERS_COLLECTION, email_hash, user_data)

            # Migrate any UserSong documents from guest to new user
            await self._migrate_user_songs(guest_user_id, new_user_id)

            # Delete the guest user document
            await self.firestore.delete_document(self.USERS_COLLECTION, guest_user_id)

            return User(
                id=new_user_id,
                email=email.lower(),
                display_name=guest_doc.get("display_name"),
                is_guest=False,
                created_at=datetime.fromisoformat(guest_doc.get("created_at", now.isoformat())),
                updated_at=now,
                total_songs_known=guest_doc.get("total_songs_known", 0),
                total_songs_sung=guest_doc.get("total_songs_sung", 0),
                last_sync_at=None,
            )

    async def _migrate_user_songs(self, from_user_id: str, to_user_id: str) -> int:
        """Migrate UserSong documents from one user to another.

        Args:
            from_user_id: Source user ID
            to_user_id: Target user ID

        Returns:
            Number of songs migrated
        """
        # Query all user_songs for the source user
        songs = await self.firestore.query_documents(
            "user_songs",
            filters=[("user_id", "==", from_user_id)],
            limit=1000,
        )

        migrated = 0
        for song in songs:
            old_id = song.get("id", f"{from_user_id}:{song.get('song_id', '')}")
            new_id = f"{to_user_id}:{song.get('song_id', '')}"

            # Check if target already has this song
            existing = await self.firestore.get_document("user_songs", new_id)
            if existing:
                # Merge play counts
                await self.firestore.update_document(
                    "user_songs",
                    new_id,
                    {
                        "play_count": existing.get("play_count", 0) + song.get("play_count", 0),
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                )
            else:
                # Move the song to new user
                song["id"] = new_id
                song["user_id"] = to_user_id
                song["updated_at"] = datetime.now(UTC).isoformat()
                await self.firestore.set_document("user_songs", new_id, song)

            # Delete the old document
            await self.firestore.delete_document("user_songs", old_id)
            migrated += 1

        return migrated

    async def collect_email(self, user_id: str, email: str) -> None:
        """Collect email for a user (typically a guest user).

        Associates the email with the user session without requiring
        email verification. The user can verify later if they want to
        access their account from another device.

        Args:
            user_id: User's ID (user_xxx or guest_xxx format)
            email: Email address to associate
        """
        now = datetime.now(UTC)

        # Find the user document
        if user_id.startswith("guest_"):
            doc_id = user_id
        else:
            # Query to find the user document
            docs = await self.firestore.query_documents(
                self.USERS_COLLECTION,
                filters=[("user_id", "==", user_id)],
                limit=1,
            )
            if not docs:
                return
            doc = docs[0]
            # Document ID is either hashed email (registered) or user_id (legacy)
            doc_id = self._hash_email(doc["email"]) if doc.get("email") else user_id

        # Update user document with collected email
        update_data = {
            "email": email.lower(),
            "email_collected_at": now.isoformat(),
            "email_verified": False,
            "updated_at": now.isoformat(),
        }

        await self.firestore.update_document(
            self.USERS_COLLECTION,
            doc_id,
            update_data,
        )

    async def update_user_profile(
        self,
        user_id: str,
        display_name: str | None = None,
    ) -> User | None:
        """Update a user's profile.

        Args:
            user_id: User's ID (user_xxx format)
            display_name: New display name (or None to keep unchanged)

        Returns:
            Updated User model or None if not found
        """
        # Query to find the user document
        docs = await self.firestore.query_documents(
            self.USERS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1,
        )

        if not docs:
            return None

        doc = docs[0]
        email_hash = self._hash_email(doc["email"])

        # Build update data
        now = datetime.now(UTC)
        update_data: dict[str, Any] = {
            "updated_at": now.isoformat(),
        }

        if display_name is not None:
            update_data["display_name"] = display_name

        await self.firestore.update_document(
            self.USERS_COLLECTION,
            email_hash,
            update_data,
        )

        # Return updated user
        return User(
            id=doc["user_id"],
            email=doc["email"],
            display_name=display_name if display_name is not None else doc.get("display_name"),
            is_admin=doc.get("is_admin", False),
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=now,
            total_songs_known=doc.get("total_songs_known", 0),
            total_songs_sung=doc.get("total_songs_sung", 0),
            last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
        )

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user and all their associated data.

        This method deletes:
        1. The user document
        2. All user_songs documents
        3. All user_artists documents

        Args:
            user_id: User's ID (user_xxx or guest_xxx format)

        Returns:
            True if user was deleted, False if not found
        """
        # Find the user document
        # For guest users, the document ID is the user_id itself
        # For verified users, the document ID is the email hash
        user_doc = None
        doc_id = None

        if user_id.startswith("guest_"):
            # Guest user - document ID is the user_id
            user_doc = await self.firestore.get_document(self.USERS_COLLECTION, user_id)
            doc_id = user_id
        else:
            # Verified user - need to find by user_id field
            docs = await self.firestore.query_documents(
                self.USERS_COLLECTION,
                filters=[("user_id", "==", user_id)],
                limit=1,
            )
            if docs:
                user_doc = docs[0]
                doc_id = user_doc.get("id")

        if not user_doc or not doc_id:
            return False

        # Delete all user_songs for this user
        user_songs = await self.firestore.query_documents(
            "user_songs",
            filters=[("user_id", "==", user_id)],
            limit=10000,
        )
        for song in user_songs:
            song_doc_id = song.get("id", f"{user_id}:{song.get('song_id', '')}")
            await self.firestore.delete_document("user_songs", song_doc_id)

        # Delete all user_artists for this user
        user_artists = await self.firestore.query_documents(
            "user_artists",
            filters=[("user_id", "==", user_id)],
            limit=10000,
        )
        for artist in user_artists:
            artist_doc_id = artist.get("id", "")
            if artist_doc_id:
                await self.firestore.delete_document("user_artists", artist_doc_id)

        # Delete the user document
        await self.firestore.delete_document(self.USERS_COLLECTION, doc_id)

        return True


# Singleton instance (lazy initialization)
_auth_service: AuthService | None = None


def get_auth_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> AuthService:
    """Get the auth service instance.

    Args:
        settings: Optional settings override (for testing)
        firestore: Optional Firestore service override (for testing)

    Returns:
        AuthService instance
    """
    global _auth_service
    if _auth_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)
        _auth_service = AuthService(settings, firestore)
    return _auth_service
