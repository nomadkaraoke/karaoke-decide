"""Configuration management for Karaoke Decide."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "staging", "production"] = "development"

    # Google Cloud
    google_cloud_project: str = ""
    google_cloud_project_number: str = ""  # Required for service account references
    gcs_bucket_name: str = "karaoke-decide-storage"
    firestore_database: str = "(default)"

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Spotify OAuth
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8000/api/services/spotify/callback"

    # Last.fm
    # Accept the workspace .envrc's ANDREW_LASTFM_* names as well as the
    # canonical LASTFM_* names, so the CLI works with Andrew's existing direnv.
    lastfm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("lastfm_api_key", "andrew_lastfm_apikey"),
    )
    lastfm_shared_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "lastfm_shared_secret", "andrew_lastfm_sharedsecret"
        ),
    )
    lastfm_username: str = Field(
        default="beveradb",
        validation_alias=AliasChoices("lastfm_username", "andrew_lastfm_username"),
    )

    # flacfetch (sourceability pre-check)
    flacfetch_api_url: str = "https://flacfetch.nomadkaraoke.com"
    flacfetch_api_key: str = ""

    # LRCLIB (lyrics text source for richness heuristics)
    lrclib_user_agent: str = "karaoke-decide-candidates/2.0 (https://nomadkaraoke.com)"

    # Postmark (transactional email)
    postmark_server_token: str = ""
    postmark_from_email: str = "noreply@nomadkaraoke.com"

    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24 * 7  # 1 week

    # Frontend
    frontend_url: str = "http://localhost:3000"

    # Cloud Tasks
    cloud_tasks_location: str = "us-central1"
    cloud_tasks_queue: str = "music-sync-queue"
    cloud_run_url: str = ""  # Set in production

    # Emulators (auto-detected)
    firestore_emulator_host: str | None = None
    storage_emulator_host: str | None = None

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == "production"

    @property
    def is_emulated(self) -> bool:
        """Check if using GCP emulators."""
        return self.firestore_emulator_host is not None

    @property
    def api_base_url(self) -> str:
        """Get the API base URL."""
        if self.is_production:
            return "https://api.decide.nomadkaraoke.com"
        return f"http://{self.api_host}:{self.api_port}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
