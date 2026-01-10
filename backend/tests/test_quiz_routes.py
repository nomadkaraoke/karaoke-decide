"""Tests for quiz API routes."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.known_songs_service import SetEnjoySingingResult


class TestGetQuizSongs:
    """Tests for GET /api/quiz/songs endpoint."""

    def test_returns_quiz_songs(self, quiz_client: TestClient) -> None:
        """Returns quiz songs for authenticated user."""
        response = quiz_client.get(
            "/api/quiz/songs",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        assert len(data["songs"]) == 3

    def test_returns_song_details(self, quiz_client: TestClient) -> None:
        """Returns proper song details."""
        response = quiz_client.get(
            "/api/quiz/songs",
            headers={"Authorization": "Bearer test-token"},
        )

        data = response.json()
        song = data["songs"][0]
        assert "id" in song
        assert "artist" in song
        assert "title" in song
        assert "decade" in song
        assert "popularity" in song
        assert "brand_count" in song

    def test_accepts_count_parameter(
        self,
        quiz_client: TestClient,
        mock_quiz_service: MagicMock,
    ) -> None:
        """Accepts count query parameter."""
        response = quiz_client.get(
            "/api/quiz/songs?count=10",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        mock_quiz_service.get_quiz_songs.assert_called_once_with(10)

    def test_validates_count_parameter(self, quiz_client: TestClient) -> None:
        """Validates count is within range."""
        # Too small
        response = quiz_client.get(
            "/api/quiz/songs?count=2",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

        # Too large
        response = quiz_client.get(
            "/api/quiz/songs?count=50",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 422

    def test_requires_authentication(self, quiz_client: TestClient) -> None:
        """Requires authentication."""
        response = quiz_client.get("/api/quiz/songs")

        assert response.status_code == 401


class TestSubmitQuiz:
    """Tests for POST /api/quiz/submit endpoint."""

    def test_submits_quiz_successfully(self, quiz_client: TestClient) -> None:
        """Submits quiz with known songs."""
        response = quiz_client.post(
            "/api/quiz/submit",
            json={
                "known_song_ids": ["1", "2"],
                "decade_preference": "1980s",
                "energy_preference": "high",
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Quiz completed successfully"
        assert data["songs_added"] == 2
        assert data["recommendations_ready"] is True

    def test_accepts_empty_song_list(self, quiz_client: TestClient) -> None:
        """Accepts empty song list."""
        response = quiz_client.post(
            "/api/quiz/submit",
            json={"known_song_ids": []},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201

    def test_accepts_optional_preferences(
        self,
        quiz_client: TestClient,
        mock_quiz_service: MagicMock,
    ) -> None:
        """Works without optional preferences."""
        response = quiz_client.post(
            "/api/quiz/submit",
            json={"known_song_ids": ["1"]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201
        # Verify service called with None for optional params
        call_args = mock_quiz_service.submit_quiz.call_args
        assert call_args[1]["decade_preference"] is None
        assert call_args[1]["energy_preference"] is None

    def test_validates_energy_preference(self, quiz_client: TestClient) -> None:
        """Validates energy preference values."""
        response = quiz_client.post(
            "/api/quiz/submit",
            json={
                "known_song_ids": [],
                "energy_preference": "invalid",
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 422

    def test_requires_authentication(self, quiz_client: TestClient) -> None:
        """Requires authentication."""
        response = quiz_client.post(
            "/api/quiz/submit",
            json={"known_song_ids": []},
        )

        assert response.status_code == 401


class TestGetQuizStatus:
    """Tests for GET /api/quiz/status endpoint."""

    def test_returns_incomplete_status(self, quiz_client: TestClient) -> None:
        """Returns incomplete status for new user."""
        response = quiz_client.get(
            "/api/quiz/status",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["completed"] is False
        assert data["completed_at"] is None
        assert data["songs_known_count"] == 0

    def test_returns_completed_status(
        self,
        quiz_client: TestClient,
        mock_quiz_service: MagicMock,
    ) -> None:
        """Returns completed status."""
        from datetime import UTC, datetime

        from backend.services.quiz_service import QuizStatus

        mock_quiz_service.get_quiz_status.return_value = QuizStatus(
            completed=True,
            completed_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
            songs_known_count=5,
        )

        response = quiz_client.get(
            "/api/quiz/status",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["completed"] is True
        assert data["completed_at"] is not None
        assert data["songs_known_count"] == 5

    def test_requires_authentication(self, quiz_client: TestClient) -> None:
        """Requires authentication."""
        response = quiz_client.get("/api/quiz/status")

        assert response.status_code == 401


class TestQuizEnjoySinging:
    """Tests for POST /api/quiz/enjoy-singing endpoint."""

    @pytest.fixture
    def mock_known_songs_service(self) -> MagicMock:
        """Mock known songs service for enjoy singing tests."""
        mock = MagicMock()
        mock.set_enjoy_singing = AsyncMock(
            return_value=SetEnjoySingingResult(
                success=True,
                song_id="1",
                artist="Queen",
                title="Bohemian Rhapsody",
                enjoy_singing=True,
                singing_tags=["crowd_pleaser"],
                singing_energy="emotional_powerhouse",
                vocal_comfort="challenging",
                notes="Love it!",
                created_new=True,
            )
        )
        return mock

    @pytest.fixture
    def quiz_enjoy_client(
        self,
        mock_catalog_service: MagicMock,
        mock_auth_service: MagicMock,
        mock_quiz_service: MagicMock,
        mock_known_songs_service: MagicMock,
    ) -> Generator[TestClient, None, None]:
        """Create test client with mocked quiz and known songs services."""
        with (
            patch(
                "backend.api.routes.catalog.get_catalog_service",
                return_value=mock_catalog_service,
            ),
            patch(
                "backend.api.deps.get_auth_service",
                return_value=mock_auth_service,
            ),
            patch(
                "backend.api.deps.get_quiz_service",
                return_value=mock_quiz_service,
            ),
            patch(
                "backend.api.deps.get_known_songs_service",
                return_value=mock_known_songs_service,
            ),
        ):
            from backend.main import app

            yield TestClient(app)

    def test_submit_enjoy_singing_success(
        self,
        quiz_enjoy_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Submit enjoy singing songs successfully."""
        response = quiz_enjoy_client.post(
            "/api/quiz/enjoy-singing",
            json={
                "songs": [
                    {
                        "song_id": "1",
                        "singing_tags": ["crowd_pleaser"],
                        "singing_energy": "emotional_powerhouse",
                        "vocal_comfort": "challenging",
                        "notes": "Love it!",
                    },
                    {
                        "song_id": "2",
                        "singing_tags": ["easy_to_sing"],
                    },
                ]
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["songs_added"] == 2  # Both songs created_new=True
        assert data["songs_updated"] == 0
        assert data["songs_failed"] == 0

    def test_submit_enjoy_singing_with_updates(
        self,
        quiz_enjoy_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Submit enjoy singing tracks both adds and updates."""
        # First call adds, second call updates
        mock_known_songs_service.set_enjoy_singing.side_effect = [
            SetEnjoySingingResult(
                success=True,
                song_id="1",
                artist="Queen",
                title="Test",
                enjoy_singing=True,
                singing_tags=[],
                singing_energy=None,
                vocal_comfort=None,
                notes=None,
                created_new=True,
            ),
            SetEnjoySingingResult(
                success=True,
                song_id="2",
                artist="Journey",
                title="Test 2",
                enjoy_singing=True,
                singing_tags=[],
                singing_energy=None,
                vocal_comfort=None,
                notes=None,
                created_new=False,
            ),
        ]

        response = quiz_enjoy_client.post(
            "/api/quiz/enjoy-singing",
            json={
                "songs": [
                    {"song_id": "1"},
                    {"song_id": "2"},
                ]
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["songs_added"] == 1
        assert data["songs_updated"] == 1
        assert data["songs_failed"] == 0

    def test_submit_enjoy_singing_with_failures(
        self,
        quiz_enjoy_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Submit enjoy singing handles failures gracefully."""
        # First call succeeds, second call fails
        mock_known_songs_service.set_enjoy_singing.side_effect = [
            SetEnjoySingingResult(
                success=True,
                song_id="1",
                artist="Queen",
                title="Test",
                enjoy_singing=True,
                singing_tags=[],
                singing_energy=None,
                vocal_comfort=None,
                notes=None,
                created_new=True,
            ),
            ValueError("Song not found"),
        ]

        response = quiz_enjoy_client.post(
            "/api/quiz/enjoy-singing",
            json={
                "songs": [
                    {"song_id": "1"},
                    {"song_id": "999"},
                ]
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["songs_added"] == 1
        assert data["songs_failed"] == 1

    def test_submit_enjoy_singing_empty_list(
        self,
        quiz_enjoy_client: TestClient,
    ) -> None:
        """Empty songs list fails validation."""
        response = quiz_enjoy_client.post(
            "/api/quiz/enjoy-singing",
            json={"songs": []},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 422

    def test_submit_enjoy_singing_requires_auth(
        self,
        quiz_enjoy_client: TestClient,
    ) -> None:
        """Requires authentication."""
        response = quiz_enjoy_client.post(
            "/api/quiz/enjoy-singing",
            json={"songs": [{"song_id": "1"}]},
        )

        assert response.status_code == 401
