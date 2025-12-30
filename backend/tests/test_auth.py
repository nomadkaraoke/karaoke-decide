"""Tests for auth API endpoints."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.auth_service import AuthenticationError


@pytest.fixture
def auth_client_with_mocks(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked auth service."""
    with (
        patch(
            "backend.api.routes.catalog.get_catalog_service",
            return_value=mock_catalog_service,
        ),
        patch(
            "backend.api.deps.get_auth_service",
            return_value=mock_auth_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)


class TestRequestMagicLink:
    """Tests for POST /api/auth/magic-link."""

    def test_success_returns_message(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return success message when email sent."""
        response = auth_client_with_mocks.post(
            "/api/auth/magic-link",
            json={"email": "test@example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    def test_invalid_email_returns_422(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return 422 for invalid email."""
        response = auth_client_with_mocks.post(
            "/api/auth/magic-link",
            json={"email": "not-an-email"},
        )

        assert response.status_code == 422

    def test_missing_email_returns_422(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return 422 when email is missing."""
        response = auth_client_with_mocks.post(
            "/api/auth/magic-link",
            json={},
        )

        assert response.status_code == 422

    def test_email_failure_returns_500(
        self,
        mock_catalog_service: MagicMock,
        mock_auth_service: MagicMock,
    ) -> None:
        """Should return 500 when email fails to send."""
        mock_auth_service.send_magic_link = AsyncMock(return_value=False)

        with (
            patch(
                "backend.api.routes.catalog.get_catalog_service",
                return_value=mock_catalog_service,
            ),
            patch(
                "backend.api.deps.get_auth_service",
                return_value=mock_auth_service,
            ),
        ):
            from backend.main import app

            client = TestClient(app)
            response = client.post(
                "/api/auth/magic-link",
                json={"email": "test@example.com"},
            )

        assert response.status_code == 500


class TestVerifyMagicLink:
    """Tests for POST /api/auth/verify."""

    def test_success_returns_jwt(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return JWT when token is valid."""
        response = auth_client_with_mocks.post(
            "/api/auth/verify",
            json={"token": "valid-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

    def test_invalid_token_returns_401(
        self,
        mock_catalog_service: MagicMock,
        mock_auth_service: MagicMock,
    ) -> None:
        """Should return 401 for invalid token."""
        mock_auth_service.verify_magic_link = AsyncMock(side_effect=AuthenticationError("Invalid or expired token"))

        with (
            patch(
                "backend.api.routes.catalog.get_catalog_service",
                return_value=mock_catalog_service,
            ),
            patch(
                "backend.api.deps.get_auth_service",
                return_value=mock_auth_service,
            ),
        ):
            from backend.main import app

            client = TestClient(app)
            response = client.post(
                "/api/auth/verify",
                json={"token": "invalid-token"},
            )

        assert response.status_code == 401
        assert "Invalid or expired token" in response.json()["detail"]

    def test_missing_token_returns_422(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return 422 when token is missing."""
        response = auth_client_with_mocks.post(
            "/api/auth/verify",
            json={},
        )

        assert response.status_code == 422


class TestGetCurrentUser:
    """Tests for GET /api/auth/me."""

    def test_success_returns_user_info(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return user info when authenticated."""
        response = auth_client_with_mocks.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer valid-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "email" in data
        assert data["email"] == "test@example.com"

    def test_missing_token_returns_401(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return 401 when no token provided."""
        response = auth_client_with_mocks.get("/api/auth/me")

        assert response.status_code == 401

    def test_invalid_token_returns_401(
        self,
        mock_catalog_service: MagicMock,
        mock_auth_service: MagicMock,
    ) -> None:
        """Should return 401 for invalid token."""
        mock_auth_service.validate_jwt.side_effect = AuthenticationError("Invalid token")

        with (
            patch(
                "backend.api.routes.catalog.get_catalog_service",
                return_value=mock_catalog_service,
            ),
            patch(
                "backend.api.deps.get_auth_service",
                return_value=mock_auth_service,
            ),
        ):
            from backend.main import app

            client = TestClient(app)
            response = client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer invalid-token"},
            )

        assert response.status_code == 401


class TestLogout:
    """Tests for POST /api/auth/logout."""

    def test_success_returns_message(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return success message when logged out."""
        response = auth_client_with_mocks.post(
            "/api/auth/logout",
            headers={"Authorization": "Bearer valid-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "logged out" in data["message"].lower()

    def test_unauthenticated_returns_401(
        self,
        auth_client_with_mocks: TestClient,
    ) -> None:
        """Should return 401 when not authenticated."""
        response = auth_client_with_mocks.post("/api/auth/logout")

        assert response.status_code == 401
