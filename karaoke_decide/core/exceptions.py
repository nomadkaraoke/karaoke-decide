"""Custom exceptions for Karaoke Decide."""


class KaraokeDecideError(Exception):
    """Base exception for all Karaoke Decide errors."""

    pass


class AuthenticationError(KaraokeDecideError):
    """Authentication failed."""

    pass


class AuthorizationError(KaraokeDecideError):
    """User not authorized for this action."""

    pass


class NotFoundError(KaraokeDecideError):
    """Resource not found."""

    pass


class ValidationError(KaraokeDecideError):
    """Validation failed."""

    pass


class ExternalServiceError(KaraokeDecideError):
    """External service (Spotify, Last.fm, etc.) failed."""

    def __init__(self, service: str, message: str):
        self.service = service
        super().__init__(f"{service}: {message}")


class RateLimitError(ExternalServiceError):
    """Rate limited by external service."""

    pass


class SyncError(KaraokeDecideError):
    """Music history sync failed."""

    pass
