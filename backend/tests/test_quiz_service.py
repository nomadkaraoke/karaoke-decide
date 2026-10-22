"""Tests for quiz service."""

from typing import Any
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
    mock.get_many = AsyncMock(return_value={})
    mock.batch_set = AsyncMock(return_value=None)
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
        mock_firestore.batch_set.assert_awaited_once()
        docs = mock_firestore.batch_set.call_args[0][1]
        assert docs["user_123:1"]["song_key"]

    @pytest.mark.asyncio
    async def test_skips_existing_user_songs(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Doesn't duplicate existing UserSong records."""
        # Mock song already exists
        mock_firestore.get_many = AsyncMock(
            return_value={"user_123:1": {"id": "user_123:1", "user_id": "user_123", "song_id": "1"}}
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
                "fans_also_like",
                "similar_artist",
                "genre_match",
                "decade_match",
                "popular_choice",
            ]


class TestCollaborativeSuggestions:
    """Tests for collaborative filtering recommendations."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_too_few_artists(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns empty dict when user has fewer than MIN_SHARED_ARTISTS."""
        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182"],  # Only 2, need 3
            exclude_artists=set(),
        )
        assert result == {}
        # Should not even query Firestore
        mock_firestore.query_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_similar_users(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns empty dict when no users share enough artists."""
        # Mock users with different artists
        mock_firestore.query_documents.return_value = [
            {"top_artist_names": ["Taylor Swift", "Ariana Grande", "Drake"]},
            {"top_artist_names": ["Metallica", "Slayer", "Megadeth"]},
        ]

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_finds_collaborative_suggestions(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns artists liked by similar users."""
        # Create 6 users who all like Green Day, Blink-182, Sum 41
        # and also like The Offspring (which user hasn't selected)
        similar_users = [
            {"top_artist_names": ["Green Day", "Blink-182", "Sum 41", "The Offspring", "NOFX"]} for _ in range(6)
        ]
        # Add some non-similar users
        other_users = [
            {"top_artist_names": ["Taylor Swift", "Ariana Grande"]},
            {"top_artist_names": ["Metallica"]},  # Too few artists
        ]
        mock_firestore.query_documents.return_value = similar_users + other_users

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        # Should find The Offspring and NOFX as collaborative suggestions
        assert len(result) >= 1
        # Check that at least one suggestion has shared artists
        for artist_name, shared_artists in result.items():
            assert len(shared_artists) > 0
            # Shared artists should be from user's selection
            for shared in shared_artists:
                assert shared.lower() in ["green day", "blink-182", "sum 41"]

    @pytest.mark.asyncio
    async def test_excludes_user_selected_artists(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Does not suggest artists user already selected."""
        # Users share Green Day, Blink-182, Sum 41 and also like NOFX
        mock_firestore.query_documents.return_value = [
            {"top_artist_names": ["Green Day", "Blink-182", "Sum 41", "NOFX"]} for _ in range(6)
        ]

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        # Should not include Green Day, Blink-182, or Sum 41 in suggestions
        for artist_name in result.keys():
            assert artist_name.lower() not in ["green day", "blink-182", "sum 41"]

    @pytest.mark.asyncio
    async def test_respects_exclude_list(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Does not suggest artists in exclude list."""
        mock_firestore.query_documents.return_value = [
            {"top_artist_names": ["Green Day", "Blink-182", "Sum 41", "NOFX", "Bad Religion"]} for _ in range(6)
        ]

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists={"nofx"},  # Exclude NOFX
        )

        # NOFX should not be in results
        for artist_name in result.keys():
            assert artist_name.lower() != "nofx"

    @pytest.mark.asyncio
    async def test_requires_minimum_similar_users(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Only suggests artists liked by MIN_SIMILAR_USERS or more users."""
        # Only 4 similar users like The Offspring (need 5)
        mock_firestore.query_documents.return_value = [
            {"top_artist_names": ["Green Day", "Blink-182", "Sum 41", "The Offspring"]} for _ in range(4)
        ]

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        # Should be empty because only 4 users like The Offspring
        assert result == {}

    @pytest.mark.asyncio
    async def test_queries_both_user_collections(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Queries only Last.fm users (never scans decide_users)."""
        # Set up mock to track calls
        mock_firestore.query_documents = AsyncMock(return_value=[])

        await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        collections_queried = [call[0][0] for call in mock_firestore.query_documents.call_args_list]
        assert collections_queried == ["lastfm_users"]

    @pytest.mark.asyncio
    async def test_includes_lastfm_users_in_suggestions(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Last.fm users contribute to collaborative suggestions."""

        async def mock_query(collection: str, **kwargs: Any) -> list[dict]:
            if collection == "lastfm_users":
                return [{"top_artist_names": ["Green Day", "Blink-182", "Sum 41", "The Offspring"]} for _ in range(6)]
            return []

        mock_firestore.query_documents = AsyncMock(side_effect=mock_query)

        result = await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        # Should find The Offspring since 6 Last.fm users like them
        assert "The Offspring" in result

    @pytest.mark.asyncio
    async def test_lastfm_users_use_array_contains_any(
        self,
        quiz_service: QuizService,
        mock_firestore: MagicMock,
    ) -> None:
        """Last.fm users query uses array_contains_any filter."""
        # Track calls to verify filter is used
        mock_firestore.query_documents = AsyncMock(return_value=[])

        await quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )

        # Find the lastfm_users call
        for call in mock_firestore.query_documents.call_args_list:
            if call[0][0] == "lastfm_users":
                filters = call[1].get("filters", [])
                # Should have array_contains_any filter
                assert len(filters) == 1
                field, op, values = filters[0]
                assert field == "artist_names_lower"
                assert op == "array_contains_any"
                # Should contain lowercased artist names
                assert "green day" in values
                assert "blink-182" in values
                assert "sum 41" in values


class TestFansAlsoLikeReason:
    """Tests for fans_also_like suggestion reason generation."""

    def test_generates_fans_also_like_reason(
        self,
        quiz_service: QuizService,
    ) -> None:
        """Generates fans_also_like reason when collaborative data exists."""
        candidate = QuizArtist(
            name="The Offspring",
            song_count=15,
            top_songs=["Self Esteem", "Pretty Fly"],
            total_brand_count=50,
            primary_decade="1990s",
            genres=["punk rock"],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=candidate,
            user_genres=[],
            user_decades=[],
            seed_artist_genres={},
            collaborative_suggestions={
                "The Offspring": ["Green Day", "Blink-182", "Sum 41"],
            },
        )

        assert reason.type == "fans_also_like"
        assert "Green Day" in reason.display_text
        assert "Blink-182" in reason.display_text

    def test_fans_also_like_takes_priority(
        self,
        quiz_service: QuizService,
    ) -> None:
        """fans_also_like has higher priority than other reasons."""
        candidate = QuizArtist(
            name="NOFX",
            song_count=15,
            top_songs=["Linoleum"],
            total_brand_count=50,
            primary_decade="1990s",
            genres=["punk rock", "pop punk"],  # Would match genre
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=candidate,
            user_genres=["punk", "rock"],  # Genre match would apply
            user_decades=["1990s"],  # Decade match would apply
            seed_artist_genres={"Green Day": ["punk", "rock"]},  # Similar artist would apply
            collaborative_suggestions={
                "NOFX": ["Green Day", "Blink-182", "Bad Religion"],
            },
        )

        # fans_also_like should win over all others
        assert reason.type == "fans_also_like"

    def test_formats_single_shared_artist(
        self,
        quiz_service: QuizService,
    ) -> None:
        """Formats display text correctly for single shared artist."""
        candidate = QuizArtist(
            name="Test Artist",
            song_count=10,
            top_songs=[],
            total_brand_count=30,
            primary_decade="2000s",
            genres=[],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=candidate,
            user_genres=[],
            user_decades=[],
            seed_artist_genres={},
            collaborative_suggestions={
                "Test Artist": ["Green Day"],
            },
        )

        assert reason.display_text == "Singers who like Green Day also chose"

    def test_formats_two_shared_artists(
        self,
        quiz_service: QuizService,
    ) -> None:
        """Formats display text correctly for two shared artists."""
        candidate = QuizArtist(
            name="Test Artist",
            song_count=10,
            top_songs=[],
            total_brand_count=30,
            primary_decade="2000s",
            genres=[],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=candidate,
            user_genres=[],
            user_decades=[],
            seed_artist_genres={},
            collaborative_suggestions={
                "Test Artist": ["Green Day", "Blink-182"],
            },
        )

        assert reason.display_text == "Singers who like Green Day & Blink-182 also chose"

    def test_formats_three_shared_artists(
        self,
        quiz_service: QuizService,
    ) -> None:
        """Formats display text correctly for three shared artists."""
        candidate = QuizArtist(
            name="Test Artist",
            song_count=10,
            top_songs=[],
            total_brand_count=30,
            primary_decade="2000s",
            genres=[],
        )

        reason = quiz_service._generate_suggestion_reason(
            artist=candidate,
            user_genres=[],
            user_decades=[],
            seed_artist_genres={},
            collaborative_suggestions={
                "Test Artist": ["Green Day", "Blink-182", "Sum 41"],
            },
        )

        assert reason.display_text == "Singers who like Green Day, Blink-182 & others also chose"


# ---------------------------------------------------------------------------
# Catalog-backed quiz (no live MusicBrainz, no decide_users scan, batched submit)
# ---------------------------------------------------------------------------


class FakeCatalogReader:
    """Just the CatalogReader calls the quiz service makes."""

    def __init__(self) -> None:
        self.similar_calls: list[list[str]] = []
        self.top_calls: list[tuple[list[str], int]] = []

    def artist_mbid(self, name: str) -> str | None:
        return "mbid-muse" if name.lower() == "muse" else None

    def similar(self, artist_keys: Any, limit_per: int = 30) -> list[tuple[str, str, float]]:
        self.similar_calls.append(list(artist_keys))
        return [
            ("green day", "nofx", 0.9),  # best score first, like CatalogReader.similar
            ("blink 182", "sum 41", 0.8),
            ("green day", "sum 41", 0.4),
        ]

    def top_songs_for_artists(self, artist_keys: Any, per_artist: int) -> list[dict[str, Any]]:
        self.top_calls.append((list(artist_keys), per_artist))
        return [
            {"song_key": f"{k}|song {n}", "artist": k.title(), "title": f"Song {n}", "artist_key": k}
            for k in artist_keys
            for n in range(per_artist)
        ]

    def kn_ids_for_song_keys(self, song_keys: Any) -> dict[str, int]:
        return {k: 1000 + i for i, k in enumerate(song_keys) if not k.endswith("song 4")}


class FakeCatalogStore:
    def __init__(self, reader: FakeCatalogReader | None) -> None:
        self.reader = reader

    def require(self) -> FakeCatalogReader:
        from backend.services.catalog_store import CatalogUnavailable

        if self.reader is None:
            raise CatalogUnavailable("warming_up")
        return self.reader


@pytest.fixture
def catalog_reader() -> FakeCatalogReader:
    return FakeCatalogReader()


@pytest.fixture
def catalog_quiz_service(
    mock_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_bigquery: MagicMock,
    catalog_reader: FakeCatalogReader,
) -> QuizService:
    return QuizService(
        settings=mock_settings,
        firestore=mock_firestore,
        bigquery_client=mock_bigquery,
        catalog_store=FakeCatalogStore(catalog_reader),  # type: ignore[arg-type]
    )


def _artist(name: str, brands: int = 50, genres: list[str] | None = None, decade: str = "1990s") -> QuizArtist:
    return QuizArtist(
        name=name,
        song_count=10,
        top_songs=["A"],
        total_brand_count=brands,
        primary_decade=decade,
        genres=genres or [],
    )


class TestPeopleAlsoLikeUsesCatalog:
    @pytest.mark.asyncio
    async def test_people_also_like_reads_no_decide_users(
        self, catalog_quiz_service: QuizService, mock_firestore: MagicMock
    ) -> None:
        await catalog_quiz_service._get_collaborative_suggestions(
            user_selected_artists=["Green Day", "Blink-182", "Sum 41"],
            exclude_artists=set(),
        )
        collections = [c[0][0] for c in mock_firestore.query_documents.call_args_list]
        assert "decide_users" not in collections
        assert collections == ["lastfm_users"]

    @pytest.mark.asyncio
    async def test_catalog_similarity_feeds_fans_also_like(
        self, catalog_quiz_service: QuizService, catalog_reader: FakeCatalogReader
    ) -> None:
        matches, scores = await catalog_quiz_service._catalog_similar_matches(
            ["Green Day", "Blink-182"], [_artist("NOFX"), _artist("Sum 41"), _artist("Queen")]
        )
        assert matches == {"NOFX": ["Green Day"], "Sum 41": ["Blink-182", "Green Day"]}
        assert scores == {"NOFX": 0.9, "Sum 41": 0.8}
        assert catalog_reader.similar_calls == [["green day", "blink 182"]]

    @pytest.mark.asyncio
    async def test_catalog_warming_up_gives_no_matches(
        self, mock_settings: BackendSettings, mock_firestore: MagicMock
    ) -> None:
        svc = QuizService(settings=mock_settings, firestore=mock_firestore, catalog_store=FakeCatalogStore(None))  # type: ignore[arg-type]
        assert await svc._catalog_similar_matches(["Green Day"], [_artist("NOFX")]) == ({}, {})


class TestSmartSuggestionsRanked:
    @pytest.mark.asyncio
    async def test_smart_suggestions_ranked_and_explained(
        self, catalog_quiz_service: QuizService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidates = [
            _artist("Queen", brands=99),
            _artist("Sum 41", brands=10),
            _artist("NOFX", brands=20),
            _artist("Abba", brands=80, decade="1970s"),
            _artist("Blur", brands=70),
        ]
        monkeypatch.setattr(catalog_quiz_service, "_fetch_artist_candidates", lambda **kw: list(candidates))
        monkeypatch.setattr(catalog_quiz_service, "_get_artist_genres_detailed", lambda names: {})
        monkeypatch.setattr(catalog_quiz_service, "_get_mlhd_similar_artists", lambda **kw: {})
        monkeypatch.setattr(
            catalog_quiz_service.listenbrainz, "find_similar_artist_matches", AsyncMock(return_value={})
        )

        runs = [
            await catalog_quiz_service.get_smart_quiz_artists(
                seed_artists=["Green Day", "Blink-182"], decades=["1970s"], count=4
            )
            for _ in range(3)
        ]
        names = [[a.name for a in r] for r in runs]
        assert names[0] == names[1] == names[2]
        # fans_also_like by score (NOFX 0.9, Sum 41 0.8), then decade_match, then popular_choice
        assert names[0] == ["NOFX", "Sum 41", "Abba", "Queen"]
        for artist in runs[0]:
            assert artist.suggestion_reason is not None
            assert artist.suggestion_reason.display_text
        assert runs[0][0].suggestion_reason.type == "fans_also_like"  # type: ignore[union-attr]
        assert "Green Day" in runs[0][0].suggestion_reason.display_text  # type: ignore[union-attr]


class TestSubmitBatched:
    @pytest.mark.asyncio
    async def test_submit_one_batch(
        self, catalog_quiz_service: QuizService, mock_firestore: MagicMock, catalog_reader: FakeCatalogReader
    ) -> None:
        mock_firestore.get_many = AsyncMock(return_value={})
        result = await catalog_quiz_service.submit_quiz(user_id="u1", known_artists=["Queen", "Abba", "Blur"])

        assert result.songs_added == 15
        assert catalog_reader.top_calls == [(["queen", "abba", "blur"], 5)]
        assert mock_firestore.get_many.await_count == 1
        assert mock_firestore.batch_set.await_count == 1
        assert not [c for c in mock_firestore.get_document.call_args_list if c[0][0] == "user_songs"]
        assert not [c for c in mock_firestore.set_document.call_args_list if c[0][0] == "user_songs"]

        collection, docs = mock_firestore.batch_set.call_args[0][:2]
        assert collection == "user_songs"
        assert len(docs) == 15
        for doc_id, doc in docs.items():
            assert doc["song_key"]
            assert doc["id"] == doc_id and doc["user_id"] == "u1" and doc["source"] == "quiz"
            assert "/" not in doc_id

    @pytest.mark.asyncio
    async def test_submit_skips_existing_docs(
        self, catalog_quiz_service: QuizService, mock_firestore: MagicMock
    ) -> None:
        mock_firestore.get_many = AsyncMock(return_value={"u1:1000": {"id": "u1:1000"}})
        result = await catalog_quiz_service.submit_quiz(user_id="u1", known_artists=["Queen"])
        assert result.songs_added == 4
        docs = mock_firestore.batch_set.call_args[0][1]
        assert "u1:1000" not in docs and len(docs) == 4

    @pytest.mark.asyncio
    async def test_submit_falls_back_to_bigquery_while_warming(
        self,
        mock_settings: BackendSettings,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        svc = QuizService(
            settings=mock_settings,
            firestore=mock_firestore,
            bigquery_client=mock_bigquery,
            catalog_store=FakeCatalogStore(None),  # type: ignore[arg-type]
        )
        result = await svc.submit_quiz(user_id="u1", known_artists=["Queen"])
        assert result.songs_added == 5  # the five mocked BigQuery rows
        docs = mock_firestore.batch_set.call_args[0][1]
        assert all(d["song_key"] for d in docs.values())
        assert mock_firestore.batch_set.await_count == 1


class TestGetProgress:
    @pytest.mark.asyncio
    async def test_returns_saved_doc(self, quiz_service: QuizService, mock_firestore: MagicMock) -> None:
        mock_firestore.get_document = AsyncMock(return_value={"step": 2})
        assert await quiz_service.get_progress("u1") == {"step": 2}
        mock_firestore.get_document.assert_awaited_once_with("quiz_progress", "u1")


# ---------------------------------------------------------------------------
# catalog.db fallback when BigQuery is disabled (local stack) or failing (prod degrade)
# ---------------------------------------------------------------------------


@pytest.fixture
def real_catalog(tmp_path: Any) -> Any:
    """A small invented catalog.db read through the real CatalogReader."""
    from karaoke_decide.catalog.build import ArtistRow, CatalogRows, SimilarRow, SongRow, build_catalog_db
    from karaoke_decide.catalog.reader import CatalogReader

    def songs(key: str, name: str, decade: str, n: int = 2) -> list[SongRow]:
        return [
            SongRow(f"{key}|tune {i}", name, f"Tune {i}", key, brand_count=2, decade=decade, lb_listeners=i)
            for i in range(1, n + 1)
        ]

    rows = CatalogRows(
        songs=[
            *songs("muse", "Muse", "2000s"),
            *songs("glass harbor", "Glass Harbor", "1980s"),
            *songs("neon tide", "Neon Tide", "1980s"),
            *songs("paper moons", "Paper Moons", "2010s"),
            *songs("velvet static", "Velvet Static", "1990s"),
        ],
        artists=[
            ArtistRow("muse", "Muse", popularity=95, genres=("rock",)),
            ArtistRow("glass harbor", "Glass Harbor", popularity=40, genres=("pop",)),
            ArtistRow("neon tide", "Neon Tide", popularity=70, genres=("rock",)),
            ArtistRow("paper moons", "Paper Moons", popularity=90, genres=("rock",)),
            ArtistRow("velvet static", "Velvet Static", popularity=99, genres=("jazz",)),
        ],
        similar=[SimilarRow("muse", "glass harbor", 0.8, "test"), SimilarRow("muse", "neon tide", 0.3, "test")],
        kn=[],
        genre_map={},
    )
    db = tmp_path / "catalog.db"
    build_catalog_db(rows, db, "r")
    reader = CatalogReader(db, None)
    yield reader
    reader.close()


def _fallback_service(
    mock_firestore: MagicMock, reader: Any, bigquery: MagicMock | None = None, disabled: bool = True
) -> QuizService:
    settings = BackendSettings(
        environment="development", google_cloud_project="test-project", bigquery_disabled=disabled
    )
    return QuizService(
        settings=settings,
        firestore=mock_firestore,
        bigquery_client=bigquery or MagicMock(),
        catalog_store=FakeCatalogStore(reader),  # type: ignore[arg-type]
    )


class TestSmartArtistsCatalogFallback:
    @pytest.mark.asyncio
    async def test_bigquery_disabled_answers_from_catalog(self, mock_firestore: MagicMock, real_catalog: Any) -> None:
        bq = MagicMock()
        svc = _fallback_service(mock_firestore, real_catalog, bq)
        artists = await svc.get_smart_quiz_artists(genres=["rock"], decades=["2010s"], seed_artists=["Muse"], count=3)
        bq.query.assert_not_called()
        by_name = {a.name: a for a in artists}
        # Similar to the pick first (best score first), then the genre/decade popular artists.
        assert [a.name for a in artists] == ["Glass Harbor", "Neon Tide", "Paper Moons"]
        assert "Muse" not in by_name  # a pick is never suggested back
        glass = by_name["Glass Harbor"].suggestion_reason
        assert glass is not None and glass.type == "fans_also_like" and glass.display_text == "Fans of Muse also like"
        paper = by_name["Paper Moons"].suggestion_reason
        assert paper is not None and paper.type == "genre_match" and paper.display_text == "Based on rock"
        assert by_name["Paper Moons"].top_songs and by_name["Paper Moons"].primary_decade == "2010s"
        assert by_name["Paper Moons"].song_count == 2 and by_name["Paper Moons"].total_brand_count == 4

    @pytest.mark.asyncio
    async def test_decade_reason_and_padding(self, mock_firestore: MagicMock, real_catalog: Any) -> None:
        svc = _fallback_service(mock_firestore, real_catalog)
        artists = await svc.get_smart_quiz_artists(decades=["1990s"], exclude_artists=["Paper Moons"], count=3)
        # Velvet Static is the only 1990s artist; the rest pads with any popular artist (no excludes).
        assert [a.name for a in artists] == ["Velvet Static", "Muse", "Neon Tide"]
        reasons = [a.suggestion_reason.type for a in artists if a.suggestion_reason]
        assert reasons == ["decade_match", "popular_choice", "popular_choice"]

    @pytest.mark.asyncio
    async def test_bigquery_error_degrades_to_catalog(self, mock_firestore: MagicMock, real_catalog: Any) -> None:
        bq = MagicMock()
        bq.query.side_effect = RuntimeError("boom")
        svc = _fallback_service(mock_firestore, real_catalog, bq, disabled=False)
        svc._get_collaborative_suggestions = AsyncMock(return_value={})  # type: ignore[method-assign]
        artists = await svc.get_smart_quiz_artists(genres=["jazz"], count=2)
        assert bq.query.called
        assert [a.name for a in artists][0] == "Velvet Static"
        assert all(a.suggestion_reason for a in artists)

    @pytest.mark.asyncio
    async def test_catalog_warming_up_returns_empty(self, mock_firestore: MagicMock) -> None:
        svc = _fallback_service(mock_firestore, None)
        assert await svc.get_smart_quiz_artists(genres=["rock"], seed_artists=["Muse"]) == []

    @pytest.mark.asyncio
    async def test_catalog_error_returns_empty(self, mock_firestore: MagicMock, real_catalog: Any) -> None:
        svc = _fallback_service(mock_firestore, real_catalog)
        real_catalog.popular_artist_keys = MagicMock(side_effect=RuntimeError("corrupt"))
        assert await svc.get_smart_quiz_artists(genres=["rock"]) == []


class TestKnownArtistMbidsFallback:
    """Quiz submit's MBID enrichment never fails the submit when BigQuery is off or failing."""

    @pytest.mark.asyncio
    async def test_bigquery_disabled_uses_catalog(self, mock_firestore: MagicMock) -> None:
        bq = MagicMock()
        svc = _fallback_service(mock_firestore, FakeCatalogReader(), bq)
        assert await svc._known_artist_mbids(["Muse", "Nobody"]) == ["mbid-muse"]
        bq.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_bigquery_error_uses_catalog(self, mock_firestore: MagicMock) -> None:
        bq = MagicMock()
        bq.query.side_effect = RuntimeError("boom")
        svc = _fallback_service(mock_firestore, FakeCatalogReader(), bq, disabled=False)
        svc._catalog_service = MagicMock()
        svc._catalog_service.lookup_mbids_by_names.side_effect = RuntimeError("boom")
        assert await svc._known_artist_mbids(["Muse"]) == ["mbid-muse"]

    @pytest.mark.asyncio
    async def test_catalog_warming_up_gives_none(self, mock_firestore: MagicMock) -> None:
        svc = _fallback_service(mock_firestore, None)
        assert await svc._known_artist_mbids(["Muse"]) == []
