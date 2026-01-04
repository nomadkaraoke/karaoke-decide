"""Tests for UserDataService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.user_data_service import UserDataService


@pytest.fixture
def mock_firestore() -> MagicMock:
    """Create a mock FirestoreService."""
    mock = MagicMock()
    mock.get_document = AsyncMock()
    mock.update_document = AsyncMock()
    mock.delete_document = AsyncMock()
    mock.query_documents = AsyncMock()
    mock.count_documents = AsyncMock()
    mock.collection = MagicMock()
    return mock


@pytest.fixture
def user_data_service(mock_firestore: MagicMock) -> UserDataService:
    """Create UserDataService with mock firestore."""
    return UserDataService(mock_firestore)


class TestGetDataSummary:
    """Tests for get_data_summary method."""

    @pytest.mark.asyncio
    async def test_returns_complete_summary(
        self, user_data_service: UserDataService, mock_firestore: MagicMock
    ) -> None:
        """Should return complete data summary."""
        # Mock user document lookup via query_documents (for non-guest users)
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_completed_at": "2024-01-01T00:00:00",
            "quiz_decade_pref": "1990s",
            "quiz_energy_pref": "high",
            "quiz_genres_pref": ["rock", "pop"],
            "quiz_artists_known": ["Queen", "The Beatles"],
        }

        # Mock query_documents: first call for _get_user_document, then user_artists, then music_services
        mock_firestore.query_documents.side_effect = [
            # _get_user_document query
            [user_doc],
            # user_artists query
            [
                {"source": "spotify", "artist_name": "Artist1"},
                {"source": "spotify", "artist_name": "Artist2"},
                {"source": "lastfm", "artist_name": "Artist3"},
            ],
            # music_services query
            [
                {
                    "service_type": "spotify",
                    "service_username": "testuser",
                    "tracks_synced": 50,
                    "last_sync_at": "2024-01-01",
                },
            ],
        ]

        # Mock song counts
        mock_firestore.count_documents.side_effect = [100, 80]

        result = await user_data_service.get_data_summary("user123")

        assert result["services"]["spotify"]["connected"] is True
        assert result["services"]["spotify"]["tracks_synced"] == 50
        assert result["services"]["lastfm"]["connected"] is False
        assert result["artists"]["total"] == 5  # 2 spotify + 1 lastfm + 2 quiz
        assert result["artists"]["by_source"]["spotify"] == 2
        assert result["artists"]["by_source"]["lastfm"] == 1
        assert result["artists"]["by_source"]["quiz"] == 2
        assert result["songs"]["total"] == 100
        assert result["songs"]["with_karaoke"] == 80
        assert result["preferences"]["completed"] is True
        assert result["preferences"]["decade"] == "1990s"

    @pytest.mark.asyncio
    async def test_handles_empty_user(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should handle user with no data."""
        # Mock query_documents: first call for _get_user_document (empty), then user_artists, then music_services
        mock_firestore.query_documents.side_effect = [[], [], []]
        mock_firestore.count_documents.side_effect = [0, 0]

        result = await user_data_service.get_data_summary("user123")

        assert result["artists"]["total"] == 0
        assert result["songs"]["total"] == 0
        assert result["preferences"]["completed"] is False


class TestGetPreferences:
    """Tests for get_preferences method."""

    @pytest.mark.asyncio
    async def test_returns_preferences(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should return user preferences."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_decade_pref": "1980s",
            "quiz_energy_pref": "chill",
            "quiz_genres_pref": ["jazz", "soul"],
        }
        mock_firestore.query_documents.return_value = [user_doc]

        result = await user_data_service.get_preferences("user123")

        assert result["decade_preference"] == "1980s"
        assert result["energy_preference"] == "chill"
        assert result["genres"] == ["jazz", "soul"]

    @pytest.mark.asyncio
    async def test_returns_defaults_for_missing_user(
        self, user_data_service: UserDataService, mock_firestore: MagicMock
    ) -> None:
        """Should return defaults when user not found."""
        mock_firestore.query_documents.return_value = []

        result = await user_data_service.get_preferences("user123")

        assert result["decade_preference"] is None
        assert result["energy_preference"] is None
        assert result["genres"] == []


class TestUpdatePreferences:
    """Tests for update_preferences method."""

    @pytest.mark.asyncio
    async def test_updates_all_preferences(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should update all preference fields."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_decade_pref": "2000s",
            "quiz_energy_pref": "medium",
            "quiz_genres_pref": ["electronic"],
        }
        # First query for _get_user_document (update), second for _get_user_document (return)
        mock_firestore.query_documents.side_effect = [[user_doc], [user_doc]]

        await user_data_service.update_preferences(
            user_id="user123",
            decade_preference="2000s",
            energy_preference="medium",
            genres=["electronic"],
        )

        mock_firestore.update_document.assert_called_once()
        call_args = mock_firestore.update_document.call_args
        assert call_args[0][0] == "decide_users"
        assert call_args[0][1] == "email_hash_123"  # Uses the doc ID from query
        assert call_args[0][2]["quiz_decade_pref"] == "2000s"
        assert call_args[0][2]["quiz_energy_pref"] == "medium"
        assert call_args[0][2]["quiz_genres_pref"] == ["electronic"]

    @pytest.mark.asyncio
    async def test_partial_update(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should only update provided fields."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_decade_pref": "1990s",
            "quiz_energy_pref": "high",
            "quiz_genres_pref": [],
        }
        # First query for _get_user_document (update), second for _get_user_document (return)
        mock_firestore.query_documents.side_effect = [[user_doc], [user_doc]]

        await user_data_service.update_preferences(
            user_id="user123",
            decade_preference="1990s",
        )

        call_args = mock_firestore.update_document.call_args
        assert "quiz_decade_pref" in call_args[0][2]
        assert "quiz_energy_pref" not in call_args[0][2]
        assert "quiz_genres_pref" not in call_args[0][2]


class TestGetAllArtists:
    """Tests for get_all_artists method."""

    @pytest.mark.asyncio
    async def test_combines_all_sources(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should combine artists from all sources."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": ["Quiz Artist", "Manual Artist"],
        }
        # First: user_artists query, second: _get_user_document query
        mock_firestore.query_documents.side_effect = [
            # user_artists query
            [
                {
                    "artist_name": "Spotify Artist",
                    "source": "spotify",
                    "rank": 1,
                    "time_range": "medium_term",
                    "popularity": 80,
                    "genres": ["rock"],
                },
            ],
            # _get_user_document query
            [user_doc],
        ]

        result = await user_data_service.get_all_artists("user123")

        assert len(result) == 3
        sources = [a["source"] for a in result]
        assert "spotify" in sources
        assert "quiz" in sources

    @pytest.mark.asyncio
    async def test_deduplicates_artists(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should not duplicate artists that appear in both synced and quiz."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": ["queen"],  # Same artist, different case
        }
        # First: user_artists query, second: _get_user_document query
        mock_firestore.query_documents.side_effect = [
            [{"artist_name": "Queen", "source": "spotify", "rank": 1}],
            [user_doc],
        ]

        result = await user_data_service.get_all_artists("user123")

        # Should only have one Queen entry (the synced one takes precedence)
        queen_entries = [a for a in result if a["artist_name"].lower() == "queen"]
        assert len(queen_entries) == 1

    @pytest.mark.asyncio
    async def test_deduplicates_synced_artists_keeps_highest_playcount(
        self, user_data_service: UserDataService, mock_firestore: MagicMock
    ) -> None:
        """Should deduplicate synced artists from same source, keeping highest playcount."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": [],
        }
        # Simulate duplicate artists from different time periods (historical data)
        mock_firestore.query_documents.side_effect = [
            [
                {"artist_name": "ABBA", "source": "lastfm", "rank": 5, "playcount": 100},
                {"artist_name": "ABBA", "source": "lastfm", "rank": 10, "playcount": 500},  # Higher
                {"artist_name": "ABBA", "source": "lastfm", "rank": 15, "playcount": 200},
                {"artist_name": "Queen", "source": "lastfm", "rank": 1, "playcount": 1000},
            ],
            [user_doc],
        ]

        result = await user_data_service.get_all_artists("user123")

        # Should only have one ABBA entry with highest playcount (500)
        abba_entries = [a for a in result if a["artist_name"].lower() == "abba"]
        assert len(abba_entries) == 1
        assert abba_entries[0]["playcount"] == 500

        # Queen should also be present
        queen_entries = [a for a in result if a["artist_name"].lower() == "queen"]
        assert len(queen_entries) == 1


class TestAddArtist:
    """Tests for add_artist method."""

    @pytest.mark.asyncio
    async def test_adds_artist(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should add artist to quiz_artists_known."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": ["Existing Artist", "New Artist"],
        }
        mock_doc = MagicMock()
        mock_doc.update = AsyncMock()
        mock_firestore.collection.return_value.document.return_value = mock_doc
        # First: _get_user_document, second: _get_user_document for return value
        mock_firestore.query_documents.side_effect = [[user_doc], [user_doc]]

        result = await user_data_service.add_artist("user123", "New Artist")

        assert result["added"] == "New Artist"
        mock_doc.update.assert_called_once()


class TestRemoveArtist:
    """Tests for remove_artist method."""

    @pytest.mark.asyncio
    async def test_removes_from_quiz_artists(
        self, user_data_service: UserDataService, mock_firestore: MagicMock
    ) -> None:
        """Should remove artist from quiz_artists_known."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": ["Artist to Remove", "Other Artist"],
        }
        mock_doc = MagicMock()
        mock_doc.update = AsyncMock()
        mock_firestore.collection.return_value.document.return_value = mock_doc
        # First: _get_user_document, second: user_artists query
        mock_firestore.query_documents.side_effect = [[user_doc], []]

        result = await user_data_service.remove_artist("user123", "Artist to Remove")

        assert result["success"] is True
        assert "quiz" in result["removed_from"]

    @pytest.mark.asyncio
    async def test_removes_from_synced_artists(
        self, user_data_service: UserDataService, mock_firestore: MagicMock
    ) -> None:
        """Should remove artist from user_artists collection."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": [],
        }
        mock_doc = MagicMock()
        mock_doc.update = AsyncMock()
        mock_firestore.collection.return_value.document.return_value = mock_doc
        # First: _get_user_document, second: user_artists query
        mock_firestore.query_documents.side_effect = [
            [user_doc],
            [{"id": "artist_doc_id", "artist_name": "Synced Artist", "source": "spotify"}],
        ]

        result = await user_data_service.remove_artist("user123", "Synced Artist")

        assert result["success"] is True
        assert "spotify" in result["removed_from"]
        mock_firestore.delete_document.assert_called_once_with("user_artists", "artist_doc_id")

    @pytest.mark.asyncio
    async def test_returns_not_found(self, user_data_service: UserDataService, mock_firestore: MagicMock) -> None:
        """Should return success=False when artist not found."""
        user_doc = {
            "id": "email_hash_123",
            "user_id": "user123",
            "quiz_artists_known": [],
        }
        # First: _get_user_document, second: user_artists query
        mock_firestore.query_documents.side_effect = [[user_doc], []]

        result = await user_data_service.remove_artist("user123", "Nonexistent Artist")

        assert result["success"] is False
        assert result["removed_from"] == []
