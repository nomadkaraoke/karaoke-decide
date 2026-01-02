"""Tests for My Data API routes."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient


class TestGetDataSummary:
    """Tests for GET /api/my/data/summary endpoint."""

    def test_returns_summary(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should return data summary."""
        response = my_data_client.get(
            "/api/my/data/summary",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["services"]["spotify"]["connected"] is True
        assert data["artists"]["total"] == 10
        assert data["songs"]["total"] == 100
        assert data["preferences"]["completed"] is True

    def test_requires_auth(self, my_data_client: TestClient) -> None:
        """Should require authentication."""
        response = my_data_client.get("/api/my/data/summary")
        assert response.status_code == 401


class TestGetAllArtists:
    """Tests for GET /api/my/data/artists endpoint."""

    def test_returns_artists(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should return all artists."""
        response = my_data_client.get(
            "/api/my/data/artists",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["artists"]) == 2
        assert data["artists"][0]["artist_name"] == "Queen"


class TestAddArtist:
    """Tests for POST /api/my/data/artists endpoint."""

    def test_adds_artist(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should add artist."""
        response = my_data_client.post(
            "/api/my/data/artists",
            headers={"Authorization": "Bearer test-token"},
            json={"artist_name": "New Artist"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["added"] == "New Artist"

    def test_validates_empty_name(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should reject empty artist name."""
        response = my_data_client.post(
            "/api/my/data/artists",
            headers={"Authorization": "Bearer test-token"},
            json={"artist_name": ""},
        )

        assert response.status_code == 422  # Validation error


class TestRemoveArtist:
    """Tests for DELETE /api/my/data/artists/{artist_name} endpoint."""

    def test_removes_artist(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should remove artist."""
        response = my_data_client.delete(
            "/api/my/data/artists/Queen",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["removed"] == "Queen"
        assert data["success"] is True

    def test_returns_404_when_not_found(
        self,
        my_data_client: TestClient,
        mock_user_data_service: MagicMock,
    ) -> None:
        """Should return 404 when artist not found."""
        # Override mock for this test
        mock_user_data_service.remove_artist.return_value = {
            "removed": "Nonexistent",
            "removed_from": [],
            "success": False,
        }

        response = my_data_client.delete(
            "/api/my/data/artists/Nonexistent",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestGetPreferences:
    """Tests for GET /api/my/data/preferences endpoint."""

    def test_returns_preferences(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should return preferences."""
        response = my_data_client.get(
            "/api/my/data/preferences",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["decade_preference"] == "1990s"
        assert data["energy_preference"] == "high"
        assert data["genres"] == ["rock", "pop"]


class TestUpdatePreferences:
    """Tests for PUT /api/my/data/preferences endpoint."""

    def test_updates_preferences(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should update preferences."""
        response = my_data_client.put(
            "/api/my/data/preferences",
            headers={"Authorization": "Bearer test-token"},
            json={
                "decade_preference": "2000s",
                "energy_preference": "medium",
                "genres": ["electronic"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["decade_preference"] == "2000s"

    def test_validates_energy_preference(
        self,
        my_data_client: TestClient,
    ) -> None:
        """Should validate energy preference values."""
        response = my_data_client.put(
            "/api/my/data/preferences",
            headers={"Authorization": "Bearer test-token"},
            json={"energy_preference": "invalid"},
        )

        assert response.status_code == 422  # Validation error
