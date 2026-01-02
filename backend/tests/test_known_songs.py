"""Tests for known songs routes and service."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.known_songs_service import (
    AddKnownSongResult,
    KnownSongsListResult,
    KnownSongsService,
)


@pytest.fixture
def sample_known_songs() -> list[dict]:
    """Sample known songs for testing."""
    return [
        {
            "id": "user_abc123def456:1",
            "user_id": "user_abc123def456",
            "song_id": "1",
            "source": "known_songs",
            "is_saved": True,
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "created_at": "2024-01-01T12:00:00+00:00",
            "updated_at": "2024-01-01T12:00:00+00:00",
        },
        {
            "id": "user_abc123def456:2",
            "user_id": "user_abc123def456",
            "song_id": "2",
            "source": "known_songs",
            "is_saved": True,
            "artist": "Journey",
            "title": "Don't Stop Believin'",
            "created_at": "2024-01-02T12:00:00+00:00",
            "updated_at": "2024-01-02T12:00:00+00:00",
        },
    ]


@pytest.fixture
def mock_known_songs_service(sample_known_songs: list[dict]) -> MagicMock:
    """Mock known songs service for API tests."""
    mock = MagicMock()
    mock.get_known_songs = AsyncMock(
        return_value=KnownSongsListResult(
            songs=sample_known_songs,
            total=2,
            page=1,
            per_page=20,
        )
    )
    mock.add_known_song = AsyncMock(
        return_value=AddKnownSongResult(
            added=True,
            song_id="1",
            artist="Queen",
            title="Bohemian Rhapsody",
            already_existed=False,
        )
    )
    mock.remove_known_song = AsyncMock(return_value=True)
    mock.bulk_add_known_songs = AsyncMock(
        return_value={
            "added": 2,
            "already_existed": 1,
            "not_found": 0,
            "total_requested": 3,
        }
    )
    return mock


@pytest.fixture
def known_songs_client(
    mock_catalog_service: MagicMock,
    mock_auth_service: MagicMock,
    mock_known_songs_service: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create test client with mocked known songs service."""
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
            "backend.api.deps.get_known_songs_service",
            return_value=mock_known_songs_service,
        ),
    ):
        from backend.main import app

        yield TestClient(app)


class TestListKnownSongs:
    """Tests for GET /api/known-songs."""

    def test_list_known_songs_success(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
        sample_known_songs: list[dict],
    ) -> None:
        """Test listing known songs returns user's songs."""
        response = known_songs_client.get(
            "/api/known-songs",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        assert len(data["songs"]) == 2
        assert data["songs"][0]["artist"] == "Queen"
        assert data["songs"][0]["source"] == "known_songs"
        assert data["total"] == 2

    def test_list_known_songs_requires_auth(
        self,
        known_songs_client: TestClient,
    ) -> None:
        """Test listing known songs requires authentication."""
        response = known_songs_client.get("/api/known-songs")

        assert response.status_code == 401

    def test_list_known_songs_with_pagination(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test listing known songs with pagination parameters."""
        response = known_songs_client.get(
            "/api/known-songs?page=2&per_page=10",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        mock_known_songs_service.get_known_songs.assert_called_once()
        call_kwargs = mock_known_songs_service.get_known_songs.call_args.kwargs
        assert call_kwargs["page"] == 2
        assert call_kwargs["per_page"] == 10


class TestAddKnownSong:
    """Tests for POST /api/known-songs."""

    def test_add_known_song_success(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test adding a known song successfully."""
        response = known_songs_client.post(
            "/api/known-songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": 1},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["added"] is True
        assert data["artist"] == "Queen"
        assert data["title"] == "Bohemian Rhapsody"

    def test_add_known_song_already_exists(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test adding a song that already exists."""
        mock_known_songs_service.add_known_song.return_value = AddKnownSongResult(
            added=False,
            song_id="1",
            artist="Queen",
            title="Bohemian Rhapsody",
            already_existed=True,
        )

        response = known_songs_client.post(
            "/api/known-songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": 1},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["added"] is False
        assert data["already_existed"] is True

    def test_add_known_song_not_found(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test adding a song that doesn't exist in catalog."""
        mock_known_songs_service.add_known_song.side_effect = ValueError("Song with ID 999 not found in catalog")

        response = known_songs_client.post(
            "/api/known-songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": 999},
        )

        assert response.status_code == 404

    def test_add_known_song_requires_auth(
        self,
        known_songs_client: TestClient,
    ) -> None:
        """Test adding known song requires authentication."""
        response = known_songs_client.post(
            "/api/known-songs",
            json={"song_id": 1},
        )

        assert response.status_code == 401


class TestBulkAddKnownSongs:
    """Tests for POST /api/known-songs/bulk."""

    def test_bulk_add_success(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test bulk adding known songs."""
        response = known_songs_client.post(
            "/api/known-songs/bulk",
            headers={"Authorization": "Bearer test-token"},
            json={"song_ids": [1, 2, 3]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["added"] == 2
        assert data["already_existed"] == 1
        assert data["not_found"] == 0
        assert data["total_requested"] == 3

    def test_bulk_add_empty_list(
        self,
        known_songs_client: TestClient,
    ) -> None:
        """Test bulk add with empty list fails validation."""
        response = known_songs_client.post(
            "/api/known-songs/bulk",
            headers={"Authorization": "Bearer test-token"},
            json={"song_ids": []},
        )

        assert response.status_code == 422


class TestRemoveKnownSong:
    """Tests for DELETE /api/known-songs/{song_id}."""

    def test_remove_known_song_success(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test removing a known song."""
        response = known_songs_client.delete(
            "/api/known-songs/1",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 204
        mock_known_songs_service.remove_known_song.assert_called_once()

    def test_remove_known_song_not_found(
        self,
        known_songs_client: TestClient,
        mock_known_songs_service: MagicMock,
    ) -> None:
        """Test removing a song that doesn't exist."""
        mock_known_songs_service.remove_known_song.return_value = False

        response = known_songs_client.delete(
            "/api/known-songs/999",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestKnownSongsServiceUnit:
    """Unit tests for KnownSongsService."""

    @pytest.fixture
    def known_songs_service(
        self,
        mock_backend_settings: MagicMock,
        mock_firestore_service: MagicMock,
    ) -> KnownSongsService:
        """Create known songs service with mocked dependencies."""
        mock_bigquery = MagicMock()
        return KnownSongsService(
            mock_backend_settings,
            mock_firestore_service,
            bigquery_client=mock_bigquery,
        )

    @pytest.mark.asyncio
    async def test_add_known_song_creates_user_song(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test adding a known song creates a UserSong record."""
        # Mock BigQuery to return song details
        mock_result = MagicMock()
        mock_result.id = "1"
        mock_result.artist = "Queen"
        mock_result.title = "Bohemian Rhapsody"
        known_songs_service._bigquery_client.query.return_value.result.return_value = [mock_result]

        # Mock Firestore to return no existing document
        mock_firestore_service.get_document.return_value = None

        result = await known_songs_service.add_known_song(
            user_id="user-123",
            song_id=1,
        )

        assert result.added is True
        assert result.song_id == "1"
        assert result.artist == "Queen"
        mock_firestore_service.set_document.assert_called_once()

        # Verify the document data
        call_args = mock_firestore_service.set_document.call_args
        doc_data = call_args[0][2]  # Third positional argument is the data
        assert doc_data["source"] == "known_songs"
        assert doc_data["is_saved"] is True

    @pytest.mark.asyncio
    async def test_add_known_song_existing(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test adding a song that already exists."""
        # Mock BigQuery to return song details
        mock_result = MagicMock()
        mock_result.id = "1"
        mock_result.artist = "Queen"
        mock_result.title = "Bohemian Rhapsody"
        known_songs_service._bigquery_client.query.return_value.result.return_value = [mock_result]

        # Mock Firestore to return existing document
        mock_firestore_service.get_document.return_value = {
            "id": "user-123:1",
            "song_id": "1",
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
        }

        result = await known_songs_service.add_known_song(
            user_id="user-123",
            song_id=1,
        )

        assert result.added is False
        assert result.already_existed is True
        mock_firestore_service.set_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_known_song_not_in_catalog(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test adding a song that doesn't exist in catalog."""
        # Mock BigQuery to return no results
        known_songs_service._bigquery_client.query.return_value.result.return_value = []

        with pytest.raises(ValueError, match="not found in catalog"):
            await known_songs_service.add_known_song(
                user_id="user-123",
                song_id=999,
            )

    @pytest.mark.asyncio
    async def test_remove_known_song_success(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test removing a known song."""
        mock_firestore_service.get_document.return_value = {
            "id": "user-123:1",
            "source": "known_songs",
        }

        result = await known_songs_service.remove_known_song(
            user_id="user-123",
            song_id=1,
        )

        assert result is True
        mock_firestore_service.delete_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_known_song_wrong_source(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test removing a song from a different source fails."""
        mock_firestore_service.get_document.return_value = {
            "id": "user-123:1",
            "source": "spotify",  # Not "known_songs"
        }

        result = await known_songs_service.remove_known_song(
            user_id="user-123",
            song_id=1,
        )

        assert result is False
        mock_firestore_service.delete_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_known_song_not_found(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test removing a non-existent song."""
        mock_firestore_service.get_document.return_value = None

        result = await known_songs_service.remove_known_song(
            user_id="user-123",
            song_id=999,
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_get_known_songs_pagination(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test listing known songs with pagination."""
        mock_firestore_service.count_documents = AsyncMock(return_value=25)
        mock_firestore_service.query_documents = AsyncMock(
            return_value=[{"id": "user-123:1", "song_id": "1", "artist": "Queen", "title": "Test"}]
        )

        result = await known_songs_service.get_known_songs(
            user_id="user-123",
            page=2,
            per_page=10,
        )

        assert result.total == 25
        assert result.page == 2
        assert result.per_page == 10

        # Verify query was called with correct offset
        call_kwargs = mock_firestore_service.query_documents.call_args.kwargs
        assert call_kwargs["offset"] == 10  # (page-1) * per_page
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_bulk_add_known_songs(
        self,
        known_songs_service: KnownSongsService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test bulk adding known songs."""

        # Mock BigQuery to return song details for both songs
        def mock_query(sql, job_config=None):
            result = MagicMock()
            # Return result for songs 1 and 2
            mock_row_1 = MagicMock()
            mock_row_1.id = "1"
            mock_row_1.artist = "Queen"
            mock_row_1.title = "Bohemian Rhapsody"
            mock_row_2 = MagicMock()
            mock_row_2.id = "2"
            mock_row_2.artist = "Journey"
            mock_row_2.title = "Don't Stop Believin'"

            result.result.return_value = [mock_row_1, mock_row_2]
            return result

        known_songs_service._bigquery_client.query.side_effect = mock_query

        # First call returns None (new), second call returns existing
        mock_firestore_service.get_document.side_effect = [None, {"id": "existing"}]

        result = await known_songs_service.bulk_add_known_songs(
            user_id="user-123",
            song_ids=[1, 2],
        )

        assert result["total_requested"] == 2
        # Both should be processed (1 added, 1 already existed)
        assert result["added"] + result["already_existed"] == 2
