"""Tests for the AuthService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.auth_service import AuthenticationError, AuthService


@pytest.fixture
def auth_settings() -> BackendSettings:
    """Create settings for auth testing."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing",
        jwt_algorithm="HS256",
        jwt_expiration_hours=24,
        magic_link_expiration_minutes=15,
        frontend_url="http://localhost:3000",
    )


@pytest.fixture
def mock_firestore() -> MagicMock:
    """Create a mock Firestore service."""
    mock = MagicMock()
    mock.get_document = AsyncMock(return_value=None)
    mock.set_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    mock.query_documents = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_email() -> MagicMock:
    """Create a mock email service."""
    mock = MagicMock()
    mock.send_magic_link = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def auth_service(
    auth_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_email: MagicMock,
) -> AuthService:
    """Create an AuthService with mocked dependencies."""
    return AuthService(auth_settings, mock_firestore, mock_email)


class TestGenerateMagicLinkToken:
    """Tests for token generation."""

    def test_generates_64_char_hex_string(self, auth_service: AuthService) -> None:
        """Token should be a 64-character hex string."""
        token = auth_service.generate_magic_link_token()
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_generates_unique_tokens(self, auth_service: AuthService) -> None:
        """Each token should be unique."""
        tokens = [auth_service.generate_magic_link_token() for _ in range(100)]
        assert len(set(tokens)) == 100


class TestStoreMagicLink:
    """Tests for storing magic links."""

    @pytest.mark.asyncio
    async def test_stores_magic_link_with_expiration(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Magic link should be stored with correct expiration."""
        await auth_service.store_magic_link("test@example.com", "test-token-123")

        mock_firestore.set_document.assert_called_once()
        call_args = mock_firestore.set_document.call_args

        assert call_args[0][0] == "magic_links"
        assert call_args[0][1] == "test-token-123"

        data = call_args[0][2]
        assert data["email"] == "test@example.com"
        assert data["used"] is False
        assert "created_at" in data
        assert "expires_at" in data


class TestVerifyMagicLink:
    """Tests for magic link verification."""

    @pytest.mark.asyncio
    async def test_returns_email_for_valid_token(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should return email for valid, unused, non-expired token."""
        now = datetime.now(UTC)
        mock_firestore.get_document.return_value = {
            "email": "test@example.com",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "used": False,
        }

        email = await auth_service.verify_magic_link("valid-token")

        assert email == "test@example.com"
        mock_firestore.update_document.assert_called_once_with(
            "magic_links",
            "valid-token",
            {"used": True},
        )

    @pytest.mark.asyncio
    async def test_raises_for_invalid_token(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should raise AuthenticationError for invalid token."""
        mock_firestore.get_document.return_value = None

        with pytest.raises(AuthenticationError, match="Invalid or expired token"):
            await auth_service.verify_magic_link("invalid-token")

    @pytest.mark.asyncio
    async def test_raises_for_used_token(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should raise AuthenticationError for already-used token."""
        now = datetime.now(UTC)
        mock_firestore.get_document.return_value = {
            "email": "test@example.com",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
            "used": True,
        }

        with pytest.raises(AuthenticationError, match="already been used"):
            await auth_service.verify_magic_link("used-token")

    @pytest.mark.asyncio
    async def test_raises_for_expired_token(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should raise AuthenticationError for expired token."""
        now = datetime.now(UTC)
        mock_firestore.get_document.return_value = {
            "email": "test@example.com",
            "created_at": (now - timedelta(minutes=20)).isoformat(),
            "expires_at": (now - timedelta(minutes=5)).isoformat(),
            "used": False,
        }

        with pytest.raises(AuthenticationError, match="expired"):
            await auth_service.verify_magic_link("expired-token")


class TestGetOrCreateUser:
    """Tests for user creation and retrieval."""

    @pytest.mark.asyncio
    async def test_creates_new_user_when_not_exists(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should create a new user when none exists."""
        mock_firestore.get_document.return_value = None

        user = await auth_service.get_or_create_user("newuser@example.com")

        assert user.email == "newuser@example.com"
        assert user.id.startswith("user_")
        mock_firestore.set_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_existing_user(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should return existing user when found."""
        now = datetime.now(UTC)
        mock_firestore.get_document.return_value = {
            "user_id": "user_existing123",
            "email": "existing@example.com",
            "display_name": "Existing User",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "total_songs_known": 50,
            "total_songs_sung": 25,
            "last_sync_at": None,
        }

        user = await auth_service.get_or_create_user("existing@example.com")

        assert user.id == "user_existing123"
        assert user.email == "existing@example.com"
        assert user.display_name == "Existing User"
        mock_firestore.set_document.assert_not_called()


class TestGetUserById:
    """Tests for user lookup by ID."""

    @pytest.mark.asyncio
    async def test_returns_user_when_found(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should return user when found by ID."""
        now = datetime.now(UTC)
        mock_firestore.query_documents.return_value = [
            {
                "user_id": "user_test123",
                "email": "test@example.com",
                "display_name": "Test User",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "total_songs_known": 10,
                "total_songs_sung": 5,
                "last_sync_at": None,
            }
        ]

        user = await auth_service.get_user_by_id("user_test123")

        assert user is not None
        assert user.id == "user_test123"
        assert user.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
    ) -> None:
        """Should return None when user not found."""
        mock_firestore.query_documents.return_value = []

        user = await auth_service.get_user_by_id("nonexistent")

        assert user is None


class TestGenerateJwt:
    """Tests for JWT generation."""

    def test_generates_valid_jwt(
        self,
        auth_service: AuthService,
    ) -> None:
        """Should generate a valid JWT with correct claims."""
        from datetime import datetime

        from karaoke_decide.core.models import User

        user = User(
            id="user_test123",
            email="test@example.com",
            display_name="Test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        token, expires_in = auth_service.generate_jwt(user)

        assert isinstance(token, str)
        assert len(token) > 0
        assert expires_in == 24 * 60 * 60  # 24 hours in seconds

    def test_raises_when_jwt_secret_not_configured(
        self,
        mock_firestore: MagicMock,
        mock_email: MagicMock,
    ) -> None:
        """Should raise ValueError when JWT secret not configured."""
        from datetime import datetime

        from karaoke_decide.core.models import User

        settings = BackendSettings(
            environment="development",
            google_cloud_project="test-project",
            jwt_secret="",  # Empty secret
        )
        service = AuthService(settings, mock_firestore, mock_email)

        user = User(
            id="user_test123",
            email="test@example.com",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with pytest.raises(ValueError, match="JWT_SECRET is not configured"):
            service.generate_jwt(user)


class TestValidateJwt:
    """Tests for JWT validation."""

    def test_validates_correct_jwt(
        self,
        auth_service: AuthService,
    ) -> None:
        """Should validate and decode a correct JWT."""
        from datetime import datetime

        from karaoke_decide.core.models import User

        user = User(
            id="user_test123",
            email="test@example.com",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        token, _ = auth_service.generate_jwt(user)
        claims = auth_service.validate_jwt(token)

        assert claims["sub"] == "user_test123"
        assert claims["email"] == "test@example.com"
        assert "iat" in claims
        assert "exp" in claims

    def test_raises_for_invalid_jwt(
        self,
        auth_service: AuthService,
    ) -> None:
        """Should raise AuthenticationError for invalid JWT."""
        with pytest.raises(AuthenticationError, match="Invalid token"):
            auth_service.validate_jwt("invalid-token")

    def test_raises_for_expired_jwt(
        self,
        mock_firestore: MagicMock,
        mock_email: MagicMock,
    ) -> None:
        """Should raise AuthenticationError for expired JWT."""
        from datetime import datetime

        from jose import jwt


        settings = BackendSettings(
            environment="development",
            google_cloud_project="test-project",
            jwt_secret="test-secret",
            jwt_expiration_hours=0,  # Immediate expiration
        )
        service = AuthService(settings, mock_firestore, mock_email)

        # Create an already-expired token
        now = datetime.now(UTC)
        payload = {
            "sub": "user_test",
            "email": "test@example.com",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        }
        expired_token = jwt.encode(payload, "test-secret", algorithm="HS256")

        with pytest.raises(AuthenticationError, match="Invalid token"):
            service.validate_jwt(expired_token)


class TestSendMagicLink:
    """Tests for the combined send magic link flow."""

    @pytest.mark.asyncio
    async def test_generates_stores_and_sends_link(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
        mock_email: MagicMock,
    ) -> None:
        """Should generate token, store it, and send email."""
        result = await auth_service.send_magic_link("test@example.com")

        assert result is True
        mock_firestore.set_document.assert_called_once()
        mock_email.send_magic_link.assert_called_once()

        # Check the token was passed to email service
        email_call = mock_email.send_magic_link.call_args
        assert email_call[0][0] == "test@example.com"
        assert len(email_call[0][1]) == 64  # Token is 64 chars

    @pytest.mark.asyncio
    async def test_returns_false_when_email_fails(
        self,
        auth_service: AuthService,
        mock_firestore: MagicMock,
        mock_email: MagicMock,
    ) -> None:
        """Should return False when email sending fails."""
        mock_email.send_magic_link.return_value = False

        result = await auth_service.send_magic_link("test@example.com")

        assert result is False
