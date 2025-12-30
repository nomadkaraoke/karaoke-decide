"""Tests for configuration management."""

import os
from unittest.mock import patch

from karaoke_decide.core.config import Settings, get_settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_environment(self) -> None:
        """Test default environment is development."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.environment == "development"

    def test_default_api_port(self) -> None:
        """Test default API port."""
        settings = Settings()
        assert settings.api_port == 8000

    def test_default_api_host(self) -> None:
        """Test default API host."""
        settings = Settings()
        assert settings.api_host == "0.0.0.0"

    def test_default_jwt_algorithm(self) -> None:
        """Test default JWT algorithm."""
        settings = Settings()
        assert settings.jwt_algorithm == "HS256"

    def test_default_jwt_expiration(self) -> None:
        """Test default JWT expiration is 1 week."""
        settings = Settings()
        assert settings.jwt_expiration_hours == 24 * 7

    def test_is_production_false_by_default(self) -> None:
        """Test is_production returns False in development."""
        settings = Settings()
        assert settings.is_production is False

    def test_is_production_true_in_production(self) -> None:
        """Test is_production returns True in production."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            settings = Settings()
            assert settings.is_production is True

    def test_is_emulated_false_by_default(self) -> None:
        """Test is_emulated returns False when no emulator host."""
        settings = Settings()
        assert settings.is_emulated is False

    def test_is_emulated_true_when_emulator_set(self) -> None:
        """Test is_emulated returns True when emulator host is set."""
        with patch.dict(os.environ, {"FIRESTORE_EMULATOR_HOST": "localhost:8080"}):
            settings = Settings()
            assert settings.is_emulated is True

    def test_api_base_url_development(self) -> None:
        """Test API base URL in development."""
        settings = Settings()
        assert settings.api_base_url == "http://0.0.0.0:8000"

    def test_api_base_url_production(self) -> None:
        """Test API base URL in production."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            settings = Settings()
            assert settings.api_base_url == "https://api.decide.nomadkaraoke.com"

    def test_default_frontend_url(self) -> None:
        """Test default frontend URL."""
        settings = Settings()
        assert settings.frontend_url == "http://localhost:3000"

    def test_default_firestore_database(self) -> None:
        """Test default Firestore database."""
        settings = Settings()
        assert settings.firestore_database == "(default)"

    def test_default_sendgrid_from_email(self) -> None:
        """Test default SendGrid from email."""
        settings = Settings()
        assert settings.sendgrid_from_email == "noreply@nomadkaraoke.com"


class TestGetSettings:
    """Tests for get_settings function."""

    def test_returns_settings_instance(self) -> None:
        """Test get_settings returns a Settings instance."""
        # Clear cache to ensure fresh settings
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_caches_settings(self) -> None:
        """Test get_settings caches the result."""
        get_settings.cache_clear()
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2
