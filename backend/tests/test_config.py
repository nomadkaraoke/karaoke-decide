"""Tests for backend configuration."""

import os
from unittest.mock import patch

from backend.config import BackendSettings, get_backend_settings


class TestBackendSettings:
    """Tests for BackendSettings class."""

    def test_inherits_from_settings(self) -> None:
        """Test that BackendSettings inherits from base Settings."""
        settings = BackendSettings()
        # Should have base Settings attributes
        assert hasattr(settings, "environment")
        assert hasattr(settings, "google_cloud_project")

    def test_default_rate_limit(self) -> None:
        """Test default rate limit values."""
        settings = BackendSettings()
        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window_seconds == 60

    def test_default_catalog_sync_interval(self) -> None:
        """Test default catalog sync interval."""
        settings = BackendSettings()
        assert settings.catalog_sync_interval_hours == 24

    def test_default_magic_link_expiration(self) -> None:
        """Test default magic link expiration (24 hours)."""
        settings = BackendSettings()
        assert settings.magic_link_expiration_minutes == 1440

    def test_custom_rate_limit(self) -> None:
        """Test custom rate limit from environment."""
        with patch.dict(os.environ, {"RATE_LIMIT_REQUESTS": "50"}):
            settings = BackendSettings()
            assert settings.rate_limit_requests == 50


class TestGetBackendSettings:
    """Tests for get_backend_settings function."""

    def test_returns_backend_settings(self) -> None:
        """Test get_backend_settings returns BackendSettings instance."""
        get_backend_settings.cache_clear()
        settings = get_backend_settings()
        assert isinstance(settings, BackendSettings)

    def test_caches_settings(self) -> None:
        """Test get_backend_settings caches the result."""
        get_backend_settings.cache_clear()
        settings1 = get_backend_settings()
        settings2 = get_backend_settings()
        assert settings1 is settings2
