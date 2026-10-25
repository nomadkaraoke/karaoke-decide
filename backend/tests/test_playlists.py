"""Tests for playlist routes."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.services.playlist_service import (
    PlaylistAccessDeniedError,
    PlaylistInfo,
    PlaylistNotFoundError,
    PlaylistService,
)


class TestListPlaylists:
    """Tests for GET /api/playlists."""

    def test_list_playlists_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
        sample_playlists: list[PlaylistInfo],
    ) -> None:
        """Test listing playlists returns user's playlists."""
        response = playlist_client.get(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "playlists" in data
        assert len(data["playlists"]) == 2
        assert data["playlists"][0]["name"] == "Friday Night Karaoke"
        assert data["playlists"][0]["song_count"] == 3

    def test_list_playlists_requires_auth(
        self,
        playlist_client: TestClient,
    ) -> None:
        """Test listing playlists requires authentication."""
        response = playlist_client.get("/api/playlists")

        assert response.status_code == 401

    def test_list_playlists_with_pagination(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test listing playlists with limit and offset."""
        response = playlist_client.get(
            "/api/playlists?limit=10&offset=5",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        mock_playlist_service.list_playlists.assert_called_once()
        call_kwargs = mock_playlist_service.list_playlists.call_args.kwargs
        assert call_kwargs["limit"] == 10
        assert call_kwargs["offset"] == 5


class TestCreatePlaylist:
    """Tests for POST /api/playlists."""

    def test_create_playlist_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test creating a playlist successfully."""
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "My New Playlist",
                "description": "A great collection",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Friday Night Karaoke"  # From mock

    def test_create_playlist_without_description(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test creating a playlist without description."""
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={"name": "Simple Playlist"},
        )

        assert response.status_code == 201

    def test_create_playlist_requires_auth(
        self,
        playlist_client: TestClient,
    ) -> None:
        """Test creating playlist requires authentication."""
        response = playlist_client.post(
            "/api/playlists",
            json={"name": "My Playlist"},
        )

        assert response.status_code == 401

    def test_create_playlist_validates_name(
        self,
        playlist_client: TestClient,
    ) -> None:
        """Test creating playlist with empty name fails."""
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={"name": ""},
        )

        assert response.status_code == 422


class TestGetPlaylist:
    """Tests for GET /api/playlists/{playlist_id}."""

    def test_get_playlist_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
        sample_playlists: list[PlaylistInfo],
    ) -> None:
        """Test getting a playlist by ID."""
        response = playlist_client.get(
            "/api/playlists/playlist-1",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "playlist-1"
        assert data["name"] == "Friday Night Karaoke"
        assert data["song_ids"] == ["1", "2", "3"]

    def test_get_playlist_not_found(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test getting non-existent playlist returns 404."""
        mock_playlist_service.get_playlist.side_effect = PlaylistNotFoundError("Not found")

        response = playlist_client.get(
            "/api/playlists/nonexistent",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404

    def test_get_playlist_access_denied(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test getting another user's playlist returns 403."""
        mock_playlist_service.get_playlist.side_effect = PlaylistAccessDeniedError("Access denied")

        response = playlist_client.get(
            "/api/playlists/other-users-playlist",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 403


class TestUpdatePlaylist:
    """Tests for PUT /api/playlists/{playlist_id}."""

    def test_update_playlist_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test updating a playlist."""
        response = playlist_client.put(
            "/api/playlists/playlist-1",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "Updated Name",
                "description": "Updated description",
            },
        )

        assert response.status_code == 200
        mock_playlist_service.update_playlist.assert_called_once()

    def test_update_playlist_partial(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test updating only the name."""
        response = playlist_client.put(
            "/api/playlists/playlist-1",
            headers={"Authorization": "Bearer test-token"},
            json={"name": "New Name Only"},
        )

        assert response.status_code == 200

    def test_update_playlist_not_found(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test updating non-existent playlist returns 404."""
        mock_playlist_service.update_playlist.side_effect = PlaylistNotFoundError("Not found")

        response = playlist_client.put(
            "/api/playlists/nonexistent",
            headers={"Authorization": "Bearer test-token"},
            json={"name": "Test"},
        )

        assert response.status_code == 404


class TestDeletePlaylist:
    """Tests for DELETE /api/playlists/{playlist_id}."""

    def test_delete_playlist_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test deleting a playlist."""
        response = playlist_client.delete(
            "/api/playlists/playlist-1",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 204
        mock_playlist_service.delete_playlist.assert_called_once()

    def test_delete_playlist_not_found(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test deleting non-existent playlist returns 404."""
        mock_playlist_service.delete_playlist.side_effect = PlaylistNotFoundError("Not found")

        response = playlist_client.delete(
            "/api/playlists/nonexistent",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestAddSongToPlaylist:
    """Tests for POST /api/playlists/{playlist_id}/songs."""

    def test_add_song_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test adding a song to a playlist."""
        response = playlist_client.post(
            "/api/playlists/playlist-1/songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": "new-song-id"},
        )

        assert response.status_code == 200
        mock_playlist_service.add_song_to_playlist.assert_called_once()

    def test_add_song_to_nonexistent_playlist(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test adding song to non-existent playlist returns 404."""
        mock_playlist_service.add_song_to_playlist.side_effect = PlaylistNotFoundError("Not found")

        response = playlist_client.post(
            "/api/playlists/nonexistent/songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": "123"},
        )

        assert response.status_code == 404

    def test_add_song_requires_song_id(
        self,
        playlist_client: TestClient,
    ) -> None:
        """Test adding song without song_id fails."""
        response = playlist_client.post(
            "/api/playlists/playlist-1/songs",
            headers={"Authorization": "Bearer test-token"},
            json={},
        )

        assert response.status_code == 422


class TestRemoveSongFromPlaylist:
    """Tests for DELETE /api/playlists/{playlist_id}/songs/{song_id}."""

    def test_remove_song_success(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test removing a song from a playlist."""
        response = playlist_client.delete(
            "/api/playlists/playlist-1/songs/song-123",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 204
        mock_playlist_service.remove_song_from_playlist.assert_called_once()

    def test_remove_song_from_nonexistent_playlist(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        """Test removing song from non-existent playlist returns 404."""
        mock_playlist_service.remove_song_from_playlist.side_effect = PlaylistNotFoundError("Not found")

        response = playlist_client.delete(
            "/api/playlists/nonexistent/songs/song-123",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 404


class TestPlaylistServiceUnit:
    """Unit tests for PlaylistService."""

    @pytest.fixture
    def playlist_service(
        self,
        mock_backend_settings: MagicMock,
        mock_firestore_service: MagicMock,
    ) -> PlaylistService:
        """Create playlist service with mocked dependencies."""
        return PlaylistService(mock_backend_settings, mock_firestore_service)

    @staticmethod
    def _txn(mock_firestore_service: MagicMock) -> list[tuple[str, str, dict]]:
        """Give the mock a run_transaction that reads get_document and records txn.set writes."""
        writes: list[tuple[str, str, dict]] = []

        class Txn:
            async def get(self, col: str, doc_id: str) -> dict | None:
                doc: dict | None = await mock_firestore_service.get_document(col, doc_id)
                return doc

            def set(self, col: str, doc_id: str, data: dict) -> None:
                writes.append((col, doc_id, data))

        async def run(fn: Any) -> Any:
            return await fn(Txn())

        mock_firestore_service.run_transaction = run
        return writes

    @pytest.mark.asyncio
    async def test_create_playlist(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test creating a playlist."""
        result = await playlist_service.create_playlist(
            user_id="user-123",
            name="Test Playlist",
            description="A test",
        )

        assert result.name == "Test Playlist"
        assert result.user_id == "user-123"
        assert result.song_count == 0
        mock_firestore_service.set_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_playlist_not_found(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test getting non-existent playlist."""
        mock_firestore_service.get_document.return_value = None

        with pytest.raises(PlaylistNotFoundError):
            await playlist_service.get_playlist("nonexistent", "user-123")

    @pytest.mark.asyncio
    async def test_get_playlist_access_denied(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test accessing another user's playlist."""
        mock_firestore_service.get_document.return_value = {
            "id": "playlist-1",
            "user_id": "other-user",
            "name": "Their Playlist",
            "description": None,
            "song_ids": [],
            "song_count": 0,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        with pytest.raises(PlaylistAccessDeniedError):
            await playlist_service.get_playlist("playlist-1", "user-123")

    @pytest.mark.asyncio
    async def test_add_song_prevents_duplicates(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test that adding duplicate song is a no-op."""
        mock_firestore_service.get_document.return_value = {
            "id": "playlist-1",
            "user_id": "user-123",
            "name": "Test",
            "description": None,
            "song_ids": ["song-1", "song-2"],
            "song_count": 2,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        writes = self._txn(mock_firestore_service)

        result = await playlist_service.add_song_to_playlist("playlist-1", "user-123", "song-1")

        # Should not write since song already exists
        assert writes == []
        assert result.song_count == 2

    @pytest.mark.asyncio
    async def test_add_song_success(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test adding a new song to playlist."""
        mock_firestore_service.get_document.return_value = {
            "id": "playlist-1",
            "user_id": "user-123",
            "name": "Test",
            "description": None,
            "song_ids": ["song-1"],
            "song_count": 1,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        writes = self._txn(mock_firestore_service)

        result = await playlist_service.add_song_to_playlist("playlist-1", "user-123", "song-2")

        assert len(writes) == 1
        assert writes[0][2]["rev"] == 1
        assert result.song_count == 2
        assert "song-2" in result.song_ids

    @pytest.mark.asyncio
    async def test_remove_song_success(
        self,
        playlist_service: PlaylistService,
        mock_firestore_service: MagicMock,
    ) -> None:
        """Test removing a song from playlist."""
        mock_firestore_service.get_document.return_value = {
            "id": "playlist-1",
            "user_id": "user-123",
            "name": "Test",
            "description": None,
            "song_ids": ["song-1", "song-2"],
            "song_count": 2,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

        writes = self._txn(mock_firestore_service)

        result = await playlist_service.remove_song_from_playlist("playlist-1", "user-123", "song-1")

        assert len(writes) == 1
        assert result.song_count == 1
        assert "song-1" not in result.song_ids


class TestOrderedItems:
    """Setlist items on the routes."""

    def test_delete_song_with_slash_key(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        response = playlist_client.delete(
            "/api/playlists/playlist-1/songs/ac%2Fdc|tnt",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 204
        assert mock_playlist_service.remove_song_from_playlist.call_args.kwargs["song_id"] == "ac/dc|tnt"

    def test_put_items_returns_items_and_rev(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        response = playlist_client.put(
            "/api/playlists/playlist-1/items",
            headers={"Authorization": "Bearer test-token"},
            json={"items": [{"song_key": None, "artist": "A", "title": "T"}], "base_rev": 3},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rev"] == 0 and body["items"] == []
        assert "user_id" not in body
        kwargs = mock_playlist_service.replace_items.call_args.kwargs
        assert kwargs["base_rev"] == 3 and kwargs["items"][0].title == "T"

    def test_put_items_keyed_empty_names_ok_but_unkeyed_empty_title_422(
        self, playlist_client: TestClient, mock_playlist_service: MagicMock
    ) -> None:
        ok = playlist_client.put(
            "/api/playlists/playlist-1/items",
            headers={"Authorization": "Bearer test-token"},
            json={"items": [{"song_key": "k|1", "artist": "", "title": ""}], "base_rev": 0},
        )
        assert ok.status_code == 200
        bad = playlist_client.put(
            "/api/playlists/playlist-1/items",
            headers={"Authorization": "Bearer test-token"},
            json={"items": [{"song_key": None, "artist": "A", "title": ""}], "base_rev": 0},
        )
        assert bad.status_code == 422

    def test_put_items_limit(self, playlist_client: TestClient) -> None:
        items = [{"song_key": None, "artist": "a", "title": str(n)} for n in range(501)]
        response = playlist_client.put(
            "/api/playlists/playlist-1/items",
            headers={"Authorization": "Bearer test-token"},
            json={"items": items, "base_rev": None},
        )
        assert response.status_code == 422

    def test_create_rejects_bad_client_id(self, playlist_client: TestClient) -> None:
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={"id": "nope", "name": "X"},
        )
        assert response.status_code == 422

    def test_create_passes_id_and_items(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={"id": "pl_abcdef123456", "name": "X", "items": [{"artist": "A", "title": "T"}]},
        )
        assert response.status_code == 201
        kwargs = mock_playlist_service.create_playlist.call_args.kwargs
        assert kwargs["playlist_id"] == "pl_abcdef123456" and len(kwargs["items"]) == 1


class TestFixRound1:
    """Review fixes: create 403, add 422 on a full list."""

    def test_create_with_other_users_id_is_403(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        mock_playlist_service.create_playlist.side_effect = PlaylistAccessDeniedError()
        response = playlist_client.post(
            "/api/playlists",
            headers={"Authorization": "Bearer test-token"},
            json={"id": "pl_abcdef123456", "name": "X"},
        )
        assert response.status_code == 403

    def test_add_song_to_full_playlist_is_422(
        self,
        playlist_client: TestClient,
        mock_playlist_service: MagicMock,
    ) -> None:
        mock_playlist_service.add_song_to_playlist.side_effect = ValueError("at most 500 items")
        response = playlist_client.post(
            "/api/playlists/playlist-1/songs",
            headers={"Authorization": "Bearer test-token"},
            json={"song_id": "a|b"},
        )
        assert response.status_code == 422
