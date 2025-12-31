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

    USERS_COLLECTION = "users"
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

    def _generate_user_id(self) -> str:
        """Generate a unique user ID.

        Returns:
            user_ prefixed random hex string
        """
        return f"user_{secrets.token_hex(12)}"

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
            user_id: User's ID (user_xxx format)

        Returns:
            User model or None if not found
        """
        # Query by user_id field
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
            email=doc["email"],
            display_name=doc.get("display_name"),
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
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=now,
            total_songs_known=doc.get("total_songs_known", 0),
            total_songs_sung=doc.get("total_songs_sung", 0),
            last_sync_at=(datetime.fromisoformat(doc["last_sync_at"]) if doc.get("last_sync_at") else None),
        )


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
