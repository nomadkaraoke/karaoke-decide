"""Tests for recommendations API routes."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient


class TestGetMySongs:
    """Tests for GET /api/my/songs endpoint."""

    def test_returns_user_songs(self, recommendations_client: TestClient) -> None:
        """Returns user's songs from listening history."""
        response = recommendations_client.get(
            "/api/my/songs",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "has_more" in data

    def test_returns_song_details(self, recommendations_client: TestClient) -> None:
        """Returns proper song details."""
        response = recommendations_client.get(
            "/api/my/songs",
            headers={"Authorization": "Bearer test-token"},
        )

        data = response.json()
        assert len(data["songs"]) == 2

        song = data["songs"][0]
        assert "id" in song
        assert "song_id" in song
        assert "artist" in song
        assert "title" in song
        assert "source" in song
        assert "play_count" in song
        assert "is_saved" in song
        assert "times_sung" in song

    def test_accepts_pagination_params(
        self,
        recommendations_client: TestClient,
        mock_recommendation_service: MagicMock,
    ) -> None:
        """Accepts pagination query parameters."""
        response = recommendations_client.get(
            "/api/my/songs?page=2&per_page=10",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        # Verify service called with correct offset
        call_args = mock_recommendation_service.get_user_songs.call_args
        assert call_args[1]["limit"] == 10
        assert call_args[1]["offset"] == 10  # (page 2 - 1) * 10

    def test_validates_pagination_params(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Validates pagination parameters."""
        # Page too small
        response = recommendations_client.get(
            "/api/my/songs?page=0",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

        # Per page too large
        response = recommendations_client.get(
            "/api/my/songs?per_page=200",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

    def test_requires_authentication(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Requires authentication."""
        response = recommendations_client.get("/api/my/songs")

        assert response.status_code == 401

    def test_returns_pagination_info(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Returns correct pagination info."""
        response = recommendations_client.get(
            "/api/my/songs?page=1&per_page=20",
            headers={"Authorization": "Bearer test-token"},
        )

        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 20
        assert data["total"] == 2
        assert data["has_more"] is False


class TestGetRecommendations:
    """Tests for GET /api/my/recommendations endpoint."""

    def test_returns_recommendations(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Returns personalized recommendations."""
        response = recommendations_client.get(
            "/api/my/recommendations",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "recommendations" in data
        assert len(data["recommendations"]) == 2

    def test_returns_recommendation_details(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Returns proper recommendation details."""
        response = recommendations_client.get(
            "/api/my/recommendations",
            headers={"Authorization": "Bearer test-token"},
        )

        data = response.json()
        rec = data["recommendations"][0]
        assert "song_id" in rec
        assert "artist" in rec
        assert "title" in rec
        assert "score" in rec
        assert "reason" in rec
        assert "reason_type" in rec
        assert "brand_count" in rec
        assert "popularity" in rec

    def test_accepts_limit_parameter(
        self,
        recommendations_client: TestClient,
        mock_recommendation_service: MagicMock,
    ) -> None:
        """Accepts limit query parameter."""
        response = recommendations_client.get(
            "/api/my/recommendations?limit=10",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        mock_recommendation_service.get_recommendations.assert_called_once()
        call_args = mock_recommendation_service.get_recommendations.call_args
        assert call_args[1]["limit"] == 10

    def test_accepts_decade_filter(
        self,
        recommendations_client: TestClient,
        mock_recommendation_service: MagicMock,
    ) -> None:
        """Accepts decade filter parameter."""
        response = recommendations_client.get(
            "/api/my/recommendations?decade=1980s",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        call_args = mock_recommendation_service.get_recommendations.call_args
        assert call_args[1]["decade"] == "1980s"

    def test_accepts_min_popularity_filter(
        self,
        recommendations_client: TestClient,
        mock_recommendation_service: MagicMock,
    ) -> None:
        """Accepts minimum popularity filter."""
        response = recommendations_client.get(
            "/api/my/recommendations?min_popularity=50",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        call_args = mock_recommendation_service.get_recommendations.call_args
        assert call_args[1]["min_popularity"] == 50

    def test_validates_limit_parameter(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Validates limit is within range."""
        # Too small
        response = recommendations_client.get(
            "/api/my/recommendations?limit=0",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

        # Too large
        response = recommendations_client.get(
            "/api/my/recommendations?limit=100",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

    def test_validates_min_popularity_parameter(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Validates min_popularity is within range."""
        response = recommendations_client.get(
            "/api/my/recommendations?min_popularity=150",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

    def test_requires_authentication(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Requires authentication."""
        response = recommendations_client.get("/api/my/recommendations")

        assert response.status_code == 401

    def test_recommendations_include_reason(
        self,
        recommendations_client: TestClient,
    ) -> None:
        """Recommendations include explanation."""
        response = recommendations_client.get(
            "/api/my/recommendations",
            headers={"Authorization": "Bearer test-token"},
        )

        data = response.json()
        for rec in data["recommendations"]:
            assert rec["reason"]  # Non-empty string
            assert rec["reason_type"] in [
                "known_artist",
                "similar_genre",
                "decade_match",
                "crowd_pleaser",
                "popular",
            ]
