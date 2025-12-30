"""Tests for quiz service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.quiz_service import QuizService


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock backend settings."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
    )


@pytest.fixture
def mock_firestore() -> MagicMock:
    """Create mock Firestore service."""
    mock = MagicMock()
    mock.get_document = AsyncMock(return_value=None)
    mock.set_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    mock.query_documents = AsyncMock(return_value=[])
    mock.count_documents = AsyncMock(return_value=0)
    return mock


@pytest.fixture
def mock_bigquery() -> MagicMock:
    """Create mock BigQuery client."""
    mock = MagicMock()

    # Mock query results
    mock_rows = []
    for i, (artist, title) in enumerate(
        [
            ("Queen", "Bohemian Rhapsody"),
            ("Journey", "Don't Stop Believin'"),
            ("Adele", "Rolling in the Deep"),
            ("ABBA", "Dancing Queen"),
            ("Whitney Houston", "I Will Always Love You"),
        ]
    ):
        row = MagicMock()
        row.id = str(i + 1)
        row.artist = artist
        row.title = title
        row.brand_count = 8 - i  # Decreasing brand counts
        row.spotify_popularity = 80 - i * 5
        mock_rows.append(row)

    mock_result = MagicMock()
    mock_result.result.return_value = mock_rows
    mock.query.return_value = mock_result

    return mock


@pytest.fixture
def quiz_service(
    mock_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_bigquery: MagicMock,
) -> QuizService:
    """Create QuizService with mocks."""
    return QuizService(
        settings=mock_settings,
        firestore=mock_firestore,
        bigquery_client=mock_bigquery,
    )


class TestGetQuizSongs:
    """Tests for get_quiz_songs method."""

    @pytest.mark.asyncio
    async def test_returns_quiz_songs(self, quiz_service: QuizService) -> None:
        """Returns quiz songs from BigQuery."""
        songs = await quiz_service.get_quiz_songs(count=5)

        assert len(songs) <= 5
        assert all(hasattr(s, "artist") for s in songs)
        assert all(hasattr(s, "title") for s in songs)

    @pytest.mark.asyncio
    async def test_ensures_artist_diversity(self, quiz_service: QuizService, mock_bigquery: MagicMock) -> None:
        """Ensures no duplicate artists in quiz songs."""
        # Add duplicate artist
        mock_rows = []
        for i in range(10):
            row = MagicMock()
            row.id = str(i + 1)
            row.artist = "Queen" if i < 5 else f"Artist {i}"
            row.title = f"Song {i}"
            row.brand_count = 8
            row.spotify_popularity = 80
            mock_rows.append(row)

        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        songs = await quiz_service.get_quiz_songs(count=5)

        # Check unique artists
        artists = [s.artist for s in songs]
        # Queen should only appear once
        assert artists.count("Queen") <= 1

    @pytest.mark.asyncio
    async def test_returns_requested_count(self, quiz_service: QuizService) -> None:
        """Returns approximately the requested number of songs."""
        songs = await quiz_service.get_quiz_songs(count=3)

        # May be less if not enough unique artists
        assert len(songs) <= 3


class TestSubmitQuiz:
    """Tests for submit_quiz method."""

    @pytest.mark.asyncio
    async def test_creates_user_songs(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Creates UserSong records for known songs."""
        # Mock BigQuery to return song details
        mock_rows = [MagicMock()]
        mock_rows[0].id = "1"
        mock_rows[0].artist = "Queen"
        mock_rows[0].title = "Bohemian Rhapsody"

        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        # Mock user query for profile update
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                }
            ]
        )

        result = await quiz_service.submit_quiz(
            user_id="user_123",
            known_song_ids=["1"],
            decade_preference="1980s",
            energy_preference="high",
        )

        assert result.songs_added == 1
        assert result.recommendations_ready is True
        mock_firestore.set_document.assert_called()

    @pytest.mark.asyncio
    async def test_skips_existing_user_songs(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Doesn't duplicate existing UserSong records."""
        # Mock song already exists
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": "user_123:1",
                "user_id": "user_123",
                "song_id": "1",
            }
        )

        # Mock BigQuery
        mock_rows = [MagicMock()]
        mock_rows[0].id = "1"
        mock_rows[0].artist = "Queen"
        mock_rows[0].title = "Bohemian Rhapsody"
        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        # Mock user query
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                }
            ]
        )

        result = await quiz_service.submit_quiz(
            user_id="user_123",
            known_song_ids=["1"],
        )

        assert result.songs_added == 0

    @pytest.mark.asyncio
    async def test_updates_user_profile(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Updates user profile with quiz data."""
        # Mock empty song list
        mock_result = MagicMock()
        mock_result.result.return_value = []
        mock_bigquery.query.return_value = mock_result

        # Mock user query
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                }
            ]
        )

        await quiz_service.submit_quiz(
            user_id="user_123",
            known_song_ids=[],
            decade_preference="1990s",
            energy_preference="medium",
        )

        # Verify update was called with quiz data
        mock_firestore.update_document.assert_called()
        call_args = mock_firestore.update_document.call_args
        update_data = call_args[0][2]
        assert "quiz_completed_at" in update_data
        assert update_data["quiz_decade_pref"] == "1990s"
        assert update_data["quiz_energy_pref"] == "medium"

    @pytest.mark.asyncio
    async def test_empty_quiz_submission(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Handles empty quiz submission."""
        # Mock empty results
        mock_result = MagicMock()
        mock_result.result.return_value = []
        mock_bigquery.query.return_value = mock_result

        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                }
            ]
        )

        result = await quiz_service.submit_quiz(
            user_id="user_123",
            known_song_ids=[],
        )

        assert result.songs_added == 0
        assert result.recommendations_ready is False


class TestGetQuizStatus:
    """Tests for get_quiz_status method."""

    @pytest.mark.asyncio
    async def test_returns_incomplete_for_new_user(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns incomplete status for user without quiz."""
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                }
            ]
        )

        status = await quiz_service.get_quiz_status("user_123")

        assert status.completed is False
        assert status.completed_at is None
        assert status.songs_known_count == 0

    @pytest.mark.asyncio
    async def test_returns_completed_status(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns completed status for user with quiz."""
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "user_id": "user_123",
                    "email": "test@example.com",
                    "quiz_completed_at": "2024-01-15T12:00:00+00:00",
                    "quiz_songs_known": ["1", "2", "3"],
                }
            ]
        )

        status = await quiz_service.get_quiz_status("user_123")

        assert status.completed is True
        assert status.completed_at is not None
        assert status.songs_known_count == 3

    @pytest.mark.asyncio
    async def test_returns_empty_for_nonexistent_user(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns default status for nonexistent user."""
        mock_firestore.query_documents = AsyncMock(return_value=[])

        status = await quiz_service.get_quiz_status("nonexistent_user")

        assert status.completed is False
        assert status.songs_known_count == 0


class TestFetchQuizCandidates:
    """Tests for _fetch_quiz_candidates method."""

    def test_queries_bigquery(self, quiz_service: QuizService, mock_bigquery: MagicMock) -> None:
        """Queries BigQuery for quiz candidates."""
        candidates = quiz_service._fetch_quiz_candidates(limit=10)

        mock_bigquery.query.assert_called()
        assert len(candidates) > 0

    def test_returns_quiz_song_objects(self, quiz_service: QuizService) -> None:
        """Returns QuizSong objects."""
        candidates = quiz_service._fetch_quiz_candidates(limit=5)

        for song in candidates:
            assert hasattr(song, "id")
            assert hasattr(song, "artist")
            assert hasattr(song, "title")
            assert hasattr(song, "brand_count")
            assert hasattr(song, "popularity")


class TestGetSongsByIds:
    """Tests for _get_songs_by_ids method."""

    def test_returns_empty_for_empty_input(self, quiz_service: QuizService) -> None:
        """Returns empty list for empty input."""
        result = quiz_service._get_songs_by_ids([])
        assert result == []

    def test_queries_bigquery_for_songs(
        self,
        quiz_service: QuizService,
        mock_bigquery: MagicMock,
    ) -> None:
        """Queries BigQuery for song details."""
        mock_rows = [MagicMock()]
        mock_rows[0].id = "1"
        mock_rows[0].artist = "Queen"
        mock_rows[0].title = "Bohemian Rhapsody"
        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        result = quiz_service._get_songs_by_ids(["1"])

        mock_bigquery.query.assert_called()
        assert len(result) == 1
        assert result[0]["artist"] == "Queen"
