"""Tests for custom exceptions."""

import pytest

from karaoke_decide.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    KaraokeDecideError,
    NotFoundError,
    RateLimitError,
    SyncError,
    ValidationError,
)


class TestKaraokeDecideError:
    """Tests for base exception."""

    def test_base_exception(self) -> None:
        """Test base exception can be raised."""
        with pytest.raises(KaraokeDecideError):
            raise KaraokeDecideError("Test error")

    def test_base_exception_message(self) -> None:
        """Test base exception preserves message."""
        error = KaraokeDecideError("Something went wrong")
        assert str(error) == "Something went wrong"


class TestAuthenticationError:
    """Tests for authentication error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that AuthenticationError inherits from base."""
        error = AuthenticationError("Invalid credentials")
        assert isinstance(error, KaraokeDecideError)

    def test_can_be_raised(self) -> None:
        """Test raising authentication error."""
        with pytest.raises(AuthenticationError):
            raise AuthenticationError("Token expired")


class TestAuthorizationError:
    """Tests for authorization error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that AuthorizationError inherits from base."""
        error = AuthorizationError("Not allowed")
        assert isinstance(error, KaraokeDecideError)


class TestNotFoundError:
    """Tests for not found error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that NotFoundError inherits from base."""
        error = NotFoundError("Song not found")
        assert isinstance(error, KaraokeDecideError)


class TestValidationError:
    """Tests for validation error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that ValidationError inherits from base."""
        error = ValidationError("Invalid email format")
        assert isinstance(error, KaraokeDecideError)


class TestExternalServiceError:
    """Tests for external service error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that ExternalServiceError inherits from base."""
        error = ExternalServiceError("spotify", "API unavailable")
        assert isinstance(error, KaraokeDecideError)

    def test_formats_message_with_service(self) -> None:
        """Test that error message includes service name."""
        error = ExternalServiceError("spotify", "Rate limited")
        assert str(error) == "spotify: Rate limited"
        assert error.service == "spotify"

    def test_preserves_service_attribute(self) -> None:
        """Test service name is accessible."""
        error = ExternalServiceError("lastfm", "Connection failed")
        assert error.service == "lastfm"


class TestRateLimitError:
    """Tests for rate limit error."""

    def test_is_external_service_error(self) -> None:
        """Test that RateLimitError inherits from ExternalServiceError."""
        error = RateLimitError("spotify", "Too many requests")
        assert isinstance(error, ExternalServiceError)
        assert isinstance(error, KaraokeDecideError)

    def test_formats_message(self) -> None:
        """Test rate limit error message."""
        error = RateLimitError("spotify", "Try again in 30 seconds")
        assert str(error) == "spotify: Try again in 30 seconds"


class TestSyncError:
    """Tests for sync error."""

    def test_is_karaoke_decide_error(self) -> None:
        """Test that SyncError inherits from base."""
        error = SyncError("Failed to sync listening history")
        assert isinstance(error, KaraokeDecideError)
