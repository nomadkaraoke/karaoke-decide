"""Backend-specific configuration."""

import os
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

    # Phase 3b music-data imports
    listenbrainz_oauth_enabled: bool = False  # LISTENBRAINZ_OAUTH_ENABLED
    metabrainz_client_id: str = ""
    metabrainz_client_secret: str = ""
    metabrainz_base_url: str = "https://metabrainz.org"
    listenbrainz_api_base: str = "https://api.listenbrainz.org/1"
    lastfm_api_base: str = "https://ws.audioscrobbler.com/2.0/"
    spotify_embed_base: str = "https://open.spotify.com"
    decide_token_key: str = ""  # DECIDE_TOKEN_KEY: Fernet key for tokens at rest
    decide_fake_externals: bool = False
    inline_tasks: bool = False  # DECIDE_INLINE_TASKS: run Cloud Tasks handlers inline (local/e2e)
    public_api_base_url: str = "https://decide.nomadkaraoke.com"
    mobile_app_scheme: str = "nomadkaraoke"
    screenshot_llm_model: str = "gemini-3.8-flash"
    lb_poll_budget_seconds: int = 1500

    @property
    def screenshot_import_enabled(self) -> bool:
        """AI Studio key present in the env (never the gcloud fallback), or fakes on."""
        return bool(os.environ.get("GEMINI_API_KEY", "").strip()) or self.decide_fake_externals

    @property
    def listenbrainz_oauth_active(self) -> bool:
        from backend.services.token_cipher import get_token_cipher  # local: avoid import cycle

        return bool(
            self.listenbrainz_oauth_enabled
            and self.metabrainz_client_id
            and self.metabrainz_client_secret
            and get_token_cipher(self).enabled
        )

    @property
    def internal_invoker_emails(self) -> set[str]:
        return {f"{self.google_cloud_project_number}-compute@developer.gserviceaccount.com"}


@lru_cache
def get_backend_settings() -> BackendSettings:
    """Get cached backend settings instance."""
    return BackendSettings()
