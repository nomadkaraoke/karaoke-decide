"""Configuration management for Karaoke Decide."""

from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOCAL_STACK_KJBOX_SECRET = "local-stack-secret"


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

    # Local-stack /api/test/* seeding routes (env DECIDE_TEST_API). The backend refuses to mount
    # them when environment is production, whatever this says.
    decide_test_api: bool = False

    # kjbox -> decide karaoke-log partner secret (env DECIDE_KJBOX_SECRET; Secret Manager
    # decide-kjbox-secret). Empty disables the partner routes (503). Use ``kjbox_secret``.
    decide_kjbox_secret: str = ""

    # Static admin tokens for scripts (env ADMIN_TOKENS, comma-separated; sent as X-Admin-Token).
    admin_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("admin_tokens", mode="before")
    @classmethod
    def _split_admin_tokens(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    # Google Cloud
    google_cloud_project: str = ""
    google_cloud_project_number: str = ""  # Required for service account references
    gcs_bucket_name: str = "karaoke-decide-storage"
    firestore_database: str = "(default)"

    # Legacy decide JWTs are accepted through this date (inclusive); gen sessions replace them.
    legacy_jwt_sunset: date = date(2026, 11, 30)

    # Recommendations catalog (main API startup; see backend/services/catalog_store.py).
    # catalog.db is published by mb-refresh behind gs://<catalog_bucket>/catalog/latest.json;
    # availability.db comes from the search service's daily song index. The *_local_dir
    # settings point at already-built files instead (local stack, tests) and win when set.
    catalog_bucket: str = "nomadkaraoke-musicbrainz-data"
    catalog_local_dir: str = ""
    song_index_bucket: str = "nomadkaraoke-kn-data"
    song_index_local_dir: str = ""
    catalog_load_timeout_s: float = 60
    catalog_dir: str = "/tmp/decide-catalog"  # download dir (in-memory on Cloud Run)

    # Voice session (backend/services/voice_data_store.py). voice.db is published by the
    # voice-pool jobs behind gs://<voice_data_bucket>/voice/latest.json and downloaded into
    # <catalog_dir>/voice/<run>/; the pointer is re-read every voice_refresh_s. voice_local_dir
    # (an already-built voice.db: local stack, tests) wins when set.
    voice_data_bucket: str = "nomadkaraoke-musicbrainz-data"
    voice_local_dir: str = ""
    voice_refresh_s: float = 600
    # Open the voice session to every signed-in user (env VOICE_SESSION_OPEN); admins always have it.
    voice_session_open: bool = False
    # Private user recordings (voice-recordings/<user_id>/<session_id>/<n>.<ext>).
    voice_recordings_bucket: str = "nomadkaraoke-voice-recordings"
    voice_recordings_local_dir: str = ""  # a directory instead of the bucket (local stack, tests)
    # Make every BigQuery client construction fail (local stack / tests: no GCP).
    bigquery_disabled: bool = False

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
        validation_alias=AliasChoices("lastfm_shared_secret", "andrew_lastfm_sharedsecret"),
    )
    # No default: set LASTFM_USERNAME (or the workspace's ANDREW_LASTFM_USERNAME)
    # so this public repo doesn't ship a personal account identifier and
    # unconfigured callers don't silently query the wrong listening history.
    lastfm_username: str = Field(
        default="",
        validation_alias=AliasChoices("lastfm_username", "andrew_lastfm_username"),
    )

    # Apple Music (MusicKit). The key is the .p8 PEM text from the Apple Developer account
    # (Secret Manager apple-musickit-private-key); the developer token is signed from it.
    apple_musickit_private_key: str = ""
    apple_musickit_key_id: str = ""
    apple_team_id: str = ""

    # flacfetch (sourceability pre-check)
    flacfetch_api_url: str = "https://flacfetch.nomadkaraoke.com"
    flacfetch_api_key: str = ""

    # LRCLIB (lyrics text source for richness heuristics)
    lrclib_user_agent: str = "karaoke-decide-candidates/2.0 (https://nomadkaraoke.com)"

    # Gemini Developer API (LLM karaoke-suitability judge for the candidates tool).
    # Key: GEMINI_API_KEY env var, else Secret Manager `gemini-api-key` via gcloud.
    candidates_llm_model: str = "gemini-3.8-flash"

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
    def kjbox_secret(self) -> str:
        """The effective kjbox partner secret. With the local-stack test API on (never in
        production) an unset secret defaults to ``local-stack-secret``, kjbox's local value."""
        secret = self.decide_kjbox_secret.strip()
        if not secret and self.decide_test_api and not self.is_production:
            return LOCAL_STACK_KJBOX_SECRET
        return secret

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
