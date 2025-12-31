"""Tests for playlist routes."""

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

        result = await playlist_service.add_song_to_playlist("playlist-1", "user-123", "song-1")

        # Should not update since song already exists
        mock_firestore_service.update_document.assert_not_called()
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

        result = await playlist_service.add_song_to_playlist("playlist-1", "user-123", "song-2")

        mock_firestore_service.update_document.assert_called_once()
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

        result = await playlist_service.remove_song_from_playlist("playlist-1", "user-123", "song-1")

        mock_firestore_service.update_document.assert_called_once()
        assert result.song_count == 1
        assert "song-1" not in result.song_ids
