"""Tests for quiz service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.quiz_service import QuizService
from karaoke_decide.core.models import QuizArtist


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


class TestSuggestionReasonGeneration:
    """Tests for suggestion reason generation."""

    def test_format_genre_names_single(self, quiz_service: QuizService) -> None:
        """Formats single genre correctly."""
        result = quiz_service._format_genre_names(["rock"])
        assert result == "rock"

    def test_format_genre_names_two(self, quiz_service: QuizService) -> None:
        """Formats two genres with ampersand."""
        result = quiz_service._format_genre_names(["rock", "punk"])
        assert result == "rock & punk"

    def test_format_genre_names_special(self, quiz_service: QuizService) -> None:
        """Formats special genre names correctly."""
        result = quiz_service._format_genre_names(["hiphop", "rnb"])
        assert result == "hip-hop & R&B"

    def test_generate_reason_similar_artist(self, quiz_service: QuizService) -> None:
        """Generates similar_artist reason when 2+ genres overlap."""
        # Use genres that match Spotify's format - the service maps "punk rock" -> punk, rock
        artist = QuizArtist(
            name="Sum 41",
            song_count=10,
            top_songs=["Fat Lip"],
            total_brand_count=50,
            primary_decade="2000s",
            # Include multiple distinct genre types that will map to our IDs
            genres=["punk rock", "pop punk", "alternative rock", "rock"],
        )
        # Green Day has punk and rock - need 2+ overlap
        seed_artist_genres = {"Green Day": ["punk", "rock", "pop"]}

        reason = quiz_service._generate_suggestion_reason(
            artist=artist,
            user_genres=[],
            user_decades=[],
            seed_artist_genres=seed_artist_genres,
        )

        assert reason.type == "similar_artist"
        assert "Green Day" in reason.display_text
        assert reason.related_to == "Green Day"

    def test_generate_reason_genre_match(self, quiz_service: QuizService) -> None:
        """Generates genre_match reason when user genres match."""
        artist = QuizArtist(
            name="Blink-182",
            song_count=15,
            top_songs=["All the Small Things"],
            total_brand_count=80,
            primary_decade="1990s",
            genres=["punk rock", "pop punk"],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=artist,
            user_genres=["punk", "rock"],
            user_decades=[],
            seed_artist_genres={},
        )

        assert reason.type == "genre_match"
        assert "punk" in reason.display_text.lower() or "rock" in reason.display_text.lower()

    def test_generate_reason_decade_match(self, quiz_service: QuizService) -> None:
        """Generates decade_match reason when decade matches."""
        artist = QuizArtist(
            name="Queen",
            song_count=50,
            top_songs=["Bohemian Rhapsody"],
            total_brand_count=200,
            primary_decade="1970s",
            genres=[],  # No genres to match
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=artist,
            user_genres=[],
            user_decades=["1970s", "1980s"],
            seed_artist_genres={},
        )

        assert reason.type == "decade_match"
        assert "1970s" in reason.display_text

    def test_generate_reason_popular_choice_fallback(self, quiz_service: QuizService) -> None:
        """Falls back to popular_choice when no other match."""
        artist = QuizArtist(
            name="Unknown Artist",
            song_count=5,
            top_songs=["Some Song"],
            total_brand_count=30,
            primary_decade="Unknown",
            genres=[],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=artist,
            user_genres=["metal"],  # Artist has no genres
            user_decades=["2010s"],  # Artist has Unknown decade
            seed_artist_genres={},
        )

        assert reason.type == "popular_choice"
        assert "Popular karaoke choice" in reason.display_text

    def test_generate_reason_priority_order(self, quiz_service: QuizService) -> None:
        """Similar artist takes priority over genre match."""
        artist = QuizArtist(
            name="Nirvana",
            song_count=20,
            top_songs=["Smells Like Teen Spirit"],
            total_brand_count=100,
            primary_decade="1990s",
            genres=["grunge", "alternative rock", "rock"],
        )

        # Has both: similar artist match AND genre match
        reason = quiz_service._generate_suggestion_reason(
            artist=artist,
            user_genres=["grunge", "rock"],  # Would match genre
            user_decades=["1990s"],  # Would match decade
            seed_artist_genres={"Pearl Jam": ["grunge", "rock"]},  # Similar artist
        )

        # Similar artist should win
        assert reason.type == "similar_artist"
        assert "Pearl Jam" in reason.display_text

    def test_add_suggestion_reasons_to_all_candidates(self, quiz_service: QuizService) -> None:
        """Adds reasons to all candidate artists."""
        candidates = [
            QuizArtist(
                name="Artist 1",
                song_count=10,
                top_songs=["Song 1"],
                total_brand_count=50,
                primary_decade="1990s",
                genres=["rock"],
            ),
            QuizArtist(
                name="Artist 2",
                song_count=20,
                top_songs=["Song 2"],
                total_brand_count=80,
                primary_decade="2000s",
                genres=["pop"],
            ),
        ]

        results = quiz_service._add_suggestion_reasons(
            candidates=candidates,
            user_genres=["rock"],
            user_decades=["1990s"],
            seed_artist_genres={},
        )

        assert len(results) == 2
        assert all(r.suggestion_reason is not None for r in results)
        # First artist should match genre, second should be popular_choice
        reason_0 = results[0].suggestion_reason
        reason_1 = results[1].suggestion_reason
        assert reason_0 is not None
        assert reason_1 is not None
        assert reason_0.type == "genre_match"
        assert reason_1.type == "popular_choice"


class TestSmartQuizArtists:
    """Tests for get_smart_quiz_artists with reasons."""

    @pytest.mark.asyncio
    async def test_returns_artists_with_reasons(
        self,
        quiz_service: QuizService,
        mock_bigquery: MagicMock,
    ) -> None:
        """Returns artists with suggestion reasons."""
        # Mock artist query results
        mock_rows = []
        for i, name in enumerate(["Green Day", "Blink-182", "Sum 41"]):
            row = MagicMock()
            row.artist_name = name
            row.song_count = 20 - i
            row.total_brand_count = 100 - i * 10
            row.top_songs = [f"{name} Song 1", f"{name} Song 2"]
            row.genres = ["punk rock", "pop punk"]
            mock_rows.append(row)

        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        artists = await quiz_service.get_smart_quiz_artists(
            genres=["punk", "rock"],
            decades=["1990s", "2000s"],
            count=3,
        )

        # All should have suggestion reasons
        assert len(artists) <= 3
        for artist in artists:
            assert artist.suggestion_reason is not None
            assert artist.suggestion_reason.type in [
                "similar_artist",
                "genre_match",
                "decade_match",
                "popular_choice",
            ]
