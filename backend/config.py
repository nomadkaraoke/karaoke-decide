"""Backend-specific configuration."""

from functools import lru_cache

from karaoke_decide.core.config import Settings


class BackendSettings(Settings):
    """Extended settings for the backend API."""

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Catalog sync
    catalog_sync_interval_hours: int = 24

    # Magic link (24 hours to allow time for email delivery and user action)
    magic_link_expiration_minutes: int = 1440


@lru_cache
def get_backend_settings() -> BackendSettings:
    """Get cached backend settings instance."""
    return BackendSettings()
