"""Tests for recommendation service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.recommendation_service import (
    RecommendationService,
    UserContext,
)


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

    # Mock query results with songs
    mock_rows = []
    for i, (artist, title) in enumerate(
        [
            ("Queen", "We Will Rock You"),
            ("Queen", "Don't Stop Me Now"),
            ("Journey", "Any Way You Want It"),
            ("Adele", "Someone Like You"),
            ("ABBA", "Mamma Mia"),
        ]
    ):
        row = MagicMock()
        row.id = str(i + 100)  # Different IDs from user's library
        row.artist = artist
        row.title = title
        row.brand_count = 7
        row.spotify_popularity = 75
        mock_rows.append(row)

    mock_result = MagicMock()
    mock_result.result.return_value = mock_rows
    mock.query.return_value = mock_result

    return mock


@pytest.fixture
def recommendation_service(
    mock_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_bigquery: MagicMock,
) -> RecommendationService:
    """Create RecommendationService with mocks."""
    return RecommendationService(
        settings=mock_settings,
        firestore=mock_firestore,
        bigquery_client=mock_bigquery,
    )


@pytest.fixture
def user_context_with_history() -> UserContext:
    """Create user context with listening history."""
    return UserContext(
        user_id="user_123",
        known_artists={"queen", "journey"},
        known_song_ids={"1", "2", "3"},
        quiz_decade_pref="1980s",
        quiz_energy_pref="high",
        total_songs=3,
    )


@pytest.fixture
def user_context_empty() -> UserContext:
    """Create empty user context (cold start)."""
    return UserContext(
        user_id="user_new",
        known_artists=set(),
        known_song_ids=set(),
        quiz_decade_pref=None,
        quiz_energy_pref=None,
        total_songs=0,
    )


class TestGetRecommendations:
    """Tests for get_recommendations method."""

    @pytest.mark.asyncio
    async def test_returns_recommendations_for_user_with_history(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns recommendations for user with listening history."""
        # Mock user songs
        mock_firestore.query_documents = AsyncMock(
            side_effect=[
                # First call: user songs
                [
                    {
                        "id": "user_123:1",
                        "user_id": "user_123",
                        "song_id": "1",
                        "artist": "Queen",
                        "title": "Bohemian Rhapsody",
                    }
                ],
                # Second call: user profile
                [
                    {
                        "user_id": "user_123",
                        "quiz_decade_pref": "1980s",
                    }
                ],
            ]
        )

        recommendations = await recommendation_service.get_recommendations(
            user_id="user_123",
            limit=10,
        )

        assert len(recommendations) > 0
        assert all(hasattr(r, "score") for r in recommendations)
        assert all(hasattr(r, "reason") for r in recommendations)

    @pytest.mark.asyncio
    async def test_returns_crowd_pleasers_for_cold_start(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns crowd pleasers for new users."""
        # Mock empty user data
        mock_firestore.query_documents = AsyncMock(return_value=[])

        recommendations = await recommendation_service.get_recommendations(
            user_id="new_user",
            limit=10,
        )

        assert len(recommendations) > 0
        # Cold start returns crowd pleasers
        assert all(r.reason_type == "crowd_pleaser" for r in recommendations)

    @pytest.mark.asyncio
    async def test_prioritizes_known_artists(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Songs from known artists score higher."""
        # Mock user with Queen in library
        mock_firestore.query_documents = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "user_123:1",
                        "user_id": "user_123",
                        "song_id": "1",
                        "artist": "Queen",
                        "title": "Bohemian Rhapsody",
                    }
                ],
                [{"user_id": "user_123"}],
            ]
        )

        recommendations = await recommendation_service.get_recommendations(
            user_id="user_123",
            limit=20,
        )

        # Known artist songs should appear first with higher scores
        known_artist_recs = [r for r in recommendations if r.reason_type == "known_artist"]
        assert len(known_artist_recs) > 0

    @pytest.mark.asyncio
    async def test_excludes_songs_already_in_library(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Doesn't recommend songs user already has."""
        # Mock user with song ID 100 in library
        mock_firestore.query_documents = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "user_123:100",
                        "user_id": "user_123",
                        "song_id": "100",  # Same as BigQuery mock
                        "artist": "Queen",
                        "title": "We Will Rock You",
                    }
                ],
                [{"user_id": "user_123"}],
            ]
        )

        recommendations = await recommendation_service.get_recommendations(
            user_id="user_123",
            limit=20,
        )

        # Song 100 should not be in recommendations
        assert all(r.song_id != "100" for r in recommendations)

    @pytest.mark.asyncio
    async def test_respects_limit(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
        mock_bigquery: MagicMock,
    ) -> None:
        """Returns at most the requested number of recommendations."""
        mock_firestore.query_documents = AsyncMock(return_value=[])

        # Mock BigQuery to return only 3 songs
        mock_rows = []
        for i, (artist, title) in enumerate(
            [
                ("Queen", "We Will Rock You"),
                ("Journey", "Any Way You Want It"),
                ("ABBA", "Mamma Mia"),
            ]
        ):
            row = MagicMock()
            row.id = str(i + 100)
            row.artist = artist
            row.title = title
            row.brand_count = 7
            row.spotify_popularity = 75
            mock_rows.append(row)

        mock_result = MagicMock()
        mock_result.result.return_value = mock_rows
        mock_bigquery.query.return_value = mock_result

        recommendations = await recommendation_service.get_recommendations(
            user_id="user_123",
            limit=3,
        )

        assert len(recommendations) <= 3


class TestGetUserSongs:
    """Tests for get_user_songs method."""

    @pytest.mark.asyncio
    async def test_returns_user_songs(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns user's songs from Firestore."""
        mock_firestore.query_documents = AsyncMock(
            return_value=[
                {
                    "id": "user_123:1",
                    "user_id": "user_123",
                    "song_id": "1",
                    "source": "spotify",
                    "play_count": 10,
                    "last_played_at": None,
                    "is_saved": True,
                    "times_sung": 2,
                    "last_sung_at": None,
                    "average_rating": None,
                    "notes": None,
                    "artist": "Queen",
                    "title": "Bohemian Rhapsody",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                }
            ]
        )
        mock_firestore.count_documents = AsyncMock(return_value=1)

        songs, total = await recommendation_service.get_user_songs(
            user_id="user_123",
            limit=20,
        )

        assert len(songs) == 1
        assert total == 1
        assert songs[0].artist == "Queen"

    @pytest.mark.asyncio
    async def test_returns_empty_for_new_user(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Returns empty list for user without songs."""
        mock_firestore.query_documents = AsyncMock(return_value=[])
        mock_firestore.count_documents = AsyncMock(return_value=0)

        songs, total = await recommendation_service.get_user_songs(
            user_id="new_user",
            limit=20,
        )

        assert len(songs) == 0
        assert total == 0

    @pytest.mark.asyncio
    async def test_paginates_results(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Supports pagination."""
        mock_firestore.query_documents = AsyncMock(return_value=[])
        mock_firestore.count_documents = AsyncMock(return_value=100)

        songs, total = await recommendation_service.get_user_songs(
            user_id="user_123",
            limit=20,
            offset=40,
        )

        # Verify pagination params passed
        call_args = mock_firestore.query_documents.call_args
        assert call_args[1]["limit"] == 20
        assert call_args[1]["offset"] == 40


class TestBuildUserContext:
    """Tests for _build_user_context method."""

    @pytest.mark.asyncio
    async def test_builds_context_from_user_songs(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Builds context from user's songs."""
        mock_firestore.query_documents = AsyncMock(
            side_effect=[
                # User songs
                [
                    {"song_id": "1", "artist": "Queen"},
                    {"song_id": "2", "artist": "Journey"},
                ],
                # User profile
                [{"user_id": "user_123", "quiz_decade_pref": "1980s"}],
            ]
        )

        context = await recommendation_service._build_user_context("user_123")

        assert "queen" in context.known_artists
        assert "journey" in context.known_artists
        assert "1" in context.known_song_ids
        assert "2" in context.known_song_ids
        assert context.total_songs == 2

    @pytest.mark.asyncio
    async def test_includes_quiz_preferences(
        self,
        recommendation_service: RecommendationService,
        mock_firestore: MagicMock,
    ) -> None:
        """Includes quiz preferences in context."""
        mock_firestore.query_documents = AsyncMock(
            side_effect=[
                [],  # No songs
                [
                    {
                        "user_id": "user_123",
                        "quiz_decade_pref": "1990s",
                        "quiz_energy_pref": "high",
                    }
                ],
            ]
        )

        context = await recommendation_service._build_user_context("user_123")

        assert context.quiz_decade_pref == "1990s"
        assert context.quiz_energy_pref == "high"


class TestCalculateScore:
    """Tests for _calculate_score method."""

    def test_known_artist_increases_score(
        self,
        recommendation_service: RecommendationService,
        user_context_with_history: UserContext,
    ) -> None:
        """Known artist significantly increases score."""
        song = {"brand_count": 5, "spotify_popularity": 50}

        score_known = recommendation_service._calculate_score(song, user_context_with_history, is_known_artist=True)
        score_unknown = recommendation_service._calculate_score(song, user_context_with_history, is_known_artist=False)

        assert score_known > score_unknown
        assert score_known - score_unknown >= 0.3  # Artist weight is 0.35

    def test_high_popularity_increases_score(
        self,
        recommendation_service: RecommendationService,
        user_context_with_history: UserContext,
    ) -> None:
        """Higher popularity increases score."""
        song_popular = {"brand_count": 5, "spotify_popularity": 100}
        song_unpopular = {"brand_count": 5, "spotify_popularity": 0}

        score_popular = recommendation_service._calculate_score(
            song_popular, user_context_with_history, is_known_artist=False
        )
        score_unpopular = recommendation_service._calculate_score(
            song_unpopular, user_context_with_history, is_known_artist=False
        )

        assert score_popular > score_unpopular

    def test_high_brand_count_increases_score(
        self,
        recommendation_service: RecommendationService,
        user_context_with_history: UserContext,
    ) -> None:
        """More karaoke brands increases score."""
        song_many_brands = {"brand_count": 10, "spotify_popularity": 50}
        song_few_brands = {"brand_count": 1, "spotify_popularity": 50}

        score_many = recommendation_service._calculate_score(
            song_many_brands, user_context_with_history, is_known_artist=False
        )
        score_few = recommendation_service._calculate_score(
            song_few_brands, user_context_with_history, is_known_artist=False
        )

        assert score_many > score_few

    def test_score_capped_at_one(
        self,
        recommendation_service: RecommendationService,
        user_context_with_history: UserContext,
    ) -> None:
        """Score is capped at 1.0."""
        song = {"brand_count": 100, "spotify_popularity": 100}

        score = recommendation_service._calculate_score(song, user_context_with_history, is_known_artist=True)

        assert score <= 1.0


class TestGetSongsByArtists:
    """Tests for _get_songs_by_artists method."""

    def test_returns_empty_for_empty_input(
        self,
        recommendation_service: RecommendationService,
    ) -> None:
        """Returns empty list for no artists."""
        result = recommendation_service._get_songs_by_artists([], limit=10)
        assert result == []

    def test_queries_bigquery(
        self,
        recommendation_service: RecommendationService,
        mock_bigquery: MagicMock,
    ) -> None:
        """Queries BigQuery for artists' songs."""
        result = recommendation_service._get_songs_by_artists(
            ["queen", "journey"],
            limit=10,
        )

        mock_bigquery.query.assert_called()
        assert len(result) > 0


class TestGetPopularSongs:
    """Tests for _get_popular_songs method."""

    def test_queries_bigquery(
        self,
        recommendation_service: RecommendationService,
        mock_bigquery: MagicMock,
    ) -> None:
        """Queries BigQuery for popular songs."""
        result = recommendation_service._get_popular_songs(limit=10)

        mock_bigquery.query.assert_called()
        assert len(result) > 0

    def test_returns_song_dicts(
        self,
        recommendation_service: RecommendationService,
    ) -> None:
        """Returns properly structured song dicts."""
        result = recommendation_service._get_popular_songs(limit=5)

        for song in result:
            assert "id" in song
            assert "artist" in song
            assert "title" in song
            assert "brand_count" in song


class TestGetCrowdPleasers:
    """Tests for _get_crowd_pleasers method."""

    @pytest.mark.asyncio
    async def test_returns_recommendations(
        self,
        recommendation_service: RecommendationService,
    ) -> None:
        """Returns Recommendation objects."""
        recommendations = await recommendation_service._get_crowd_pleasers(limit=5)

        assert len(recommendations) > 0
        for rec in recommendations:
            assert rec.reason_type == "crowd_pleaser"
            assert rec.score == 0.5  # Neutral cold start score
