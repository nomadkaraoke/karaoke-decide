"""Tests for sync service."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.sync_service import SyncService
from backend.services.track_matcher import MatchedTrack
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import MusicService


@pytest.fixture
def mock_settings() -> BackendSettings:
    """Create mock backend settings."""
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        spotify_client_id="test-client-id",
        spotify_client_secret="test-client-secret",
        lastfm_api_key="test-lastfm-key",
    )


@pytest.fixture
def mock_firestore() -> MagicMock:
    """Create mock Firestore service."""
    mock = MagicMock()
    mock.get_document = AsyncMock(return_value=None)
    mock.set_document = AsyncMock(return_value=None)
    mock.update_document = AsyncMock(return_value=None)
    mock.query_documents = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_music_service_service() -> MagicMock:
    """Create mock music service service."""
    mock = MagicMock()
    mock.get_user_services = AsyncMock(return_value=[])
    mock.get_valid_spotify_token = AsyncMock(return_value="valid-token")
    mock.update_sync_status = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def mock_spotify_client() -> MagicMock:
    """Create mock Spotify client."""
    mock = MagicMock()
    mock.get_saved_tracks = AsyncMock(
        return_value={
            "items": [
                {
                    "track": {
                        "name": "Bohemian Rhapsody",
                        "artists": [{"name": "Queen"}],
                    }
                },
                {
                    "track": {
                        "name": "Don't Stop Me Now",
                        "artists": [{"name": "Queen"}],
                    }
                },
            ]
        }
    )
    mock.get_top_tracks = AsyncMock(
        return_value={
            "items": [
                {
                    "name": "Under Pressure",
                    "artists": [{"name": "Queen"}, {"name": "David Bowie"}],
                }
            ]
        }
    )
    mock.get_recently_played = AsyncMock(
        return_value={
            "items": [
                {
                    "track": {
                        "name": "We Will Rock You",
                        "artists": [{"name": "Queen"}],
                    }
                }
            ]
        }
    )
    return mock


@pytest.fixture
def mock_lastfm_client() -> MagicMock:
    """Create mock Last.fm client."""
    mock = MagicMock()
    mock.get_top_tracks = AsyncMock(
        return_value={
            "toptracks": {
                "track": [
                    {"name": "Song 1", "artist": {"name": "Artist 1"}, "playcount": "100"},
                    {"name": "Song 2", "artist": {"name": "Artist 2"}, "playcount": "50"},
                ]
            }
        }
    )
    # New method for fetching all top tracks with play counts and pagination
    mock.get_all_top_tracks = AsyncMock(
        return_value=[
            {"name": "Song 1", "artist": {"name": "Artist 1"}, "playcount": "100", "rank": 1},
            {"name": "Song 2", "artist": {"name": "Artist 2"}, "playcount": "50", "rank": 2},
        ]
    )
    # New method for fetching all top artists with play counts and pagination
    mock.get_all_top_artists = AsyncMock(
        return_value=[
            {"name": "Artist 1", "playcount": "500", "rank": 1},
            {"name": "Artist 2", "playcount": "300", "rank": 2},
        ]
    )
    mock.get_loved_tracks = AsyncMock(
        return_value={
            "lovedtracks": {
                "track": [
                    {"name": "Loved Song", "artist": {"name": "Loved Artist"}},
                ]
            }
        }
    )
    mock.get_recent_tracks = AsyncMock(
        return_value={
            "recenttracks": {
                "track": [
                    {"name": "Recent Song", "artist": {"#text": "Recent Artist"}},
                ]
            }
        }
    )

    # Mock for full scrobble history - async generator
    async def mock_scrobbles(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Mock async generator for get_all_scrobbles."""
        scrobbles = [
            {"name": "Scrobble Song 1", "artist": {"name": "Scrobble Artist 1"}},
            {"name": "Scrobble Song 2", "artist": {"name": "Scrobble Artist 2"}},
            {"name": "Song 1", "artist": {"name": "Artist 1"}},  # Duplicate of top track
        ]
        for scrobble in scrobbles:
            yield scrobble

    mock.get_all_scrobbles = mock_scrobbles
    return mock


@pytest.fixture
def mock_track_matcher() -> MagicMock:
    """Create mock track matcher."""
    mock = MagicMock()
    # Create mock catalog song
    mock_song = MagicMock()
    mock_song.id = "123"
    mock_song.artist = "Queen"
    mock_song.title = "Bohemian Rhapsody"

    # Return matched tracks
    mock.batch_match_tracks = AsyncMock(
        return_value=[
            MatchedTrack(
                original_artist="Queen",
                original_title="Bohemian Rhapsody",
                normalized_artist="queen",
                normalized_title="bohemian rhapsody",
                catalog_song=mock_song,
                match_confidence=1.0,
            ),
            MatchedTrack(
                original_artist="Unknown",
                original_title="Unknown Song",
                normalized_artist="unknown",
                normalized_title="unknown song",
                catalog_song=None,  # No match
                match_confidence=0.0,
            ),
        ]
    )
    return mock


@pytest.fixture
def sync_service(
    mock_settings: BackendSettings,
    mock_firestore: MagicMock,
    mock_music_service_service: MagicMock,
    mock_spotify_client: MagicMock,
    mock_lastfm_client: MagicMock,
    mock_track_matcher: MagicMock,
) -> SyncService:
    """Create SyncService with all mocks."""
    return SyncService(
        settings=mock_settings,
        firestore=mock_firestore,
        music_service_service=mock_music_service_service,
        spotify_client=mock_spotify_client,
        lastfm_client=mock_lastfm_client,
        track_matcher=mock_track_matcher,
    )


@pytest.fixture
def sample_spotify_service() -> MusicService:
    """Create sample Spotify MusicService."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_123_spotify",
        user_id="user_123",
        service_type="spotify",
        service_user_id="spotify-456",
        service_username="TestUser",
        access_token="valid-token",
        refresh_token="refresh-token",
        token_expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_lastfm_service() -> MusicService:
    """Create sample Last.fm MusicService."""
    now = datetime.now(UTC)
    return MusicService(
        id="user_123_lastfm",
        user_id="user_123",
        service_type="lastfm",
        service_user_id="lastfmuser",
        service_username="lastfmuser",
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_at=now,
        updated_at=now,
    )


class TestSyncAllServices:
    """Tests for sync_all_services method."""

    @pytest.mark.asyncio
    async def test_no_services(self, sync_service: SyncService, mock_music_service_service: MagicMock) -> None:
        """Returns empty list when no services connected."""
        mock_music_service_service.get_user_services = AsyncMock(return_value=[])

        results = await sync_service.sync_all_services("user_123")

        assert results == []

    @pytest.mark.asyncio
    async def test_syncs_multiple_services(
        self,
        sync_service: SyncService,
        mock_music_service_service: MagicMock,
        sample_spotify_service: MusicService,
        sample_lastfm_service: MusicService,
    ) -> None:
        """Syncs all connected services."""
        mock_music_service_service.get_user_services = AsyncMock(
            return_value=[sample_spotify_service, sample_lastfm_service]
        )

        results = await sync_service.sync_all_services("user_123")

        assert len(results) == 2
        assert results[0].service_type == "spotify"
        assert results[1].service_type == "lastfm"


class TestSyncSpotify:
    """Tests for sync_spotify method."""

    @pytest.mark.asyncio
    async def test_successful_sync(
        self,
        sync_service: SyncService,
        mock_music_service_service: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Successful Spotify sync returns counts."""
        result = await sync_service.sync_spotify("user_123", sample_spotify_service)

        assert result.service_type == "spotify"
        assert result.tracks_fetched > 0
        assert result.error is None

        # Verify sync status was updated
        assert mock_music_service_service.update_sync_status.call_count >= 2

    @pytest.mark.asyncio
    async def test_sync_creates_user_songs(
        self,
        sync_service: SyncService,
        mock_firestore: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Creates UserSong records for matched tracks."""
        result = await sync_service.sync_spotify("user_123", sample_spotify_service)

        # Should create or update user songs for matched tracks
        assert (result.user_songs_created + result.user_songs_updated) > 0
        mock_firestore.set_document.assert_called()

    @pytest.mark.asyncio
    async def test_sync_updates_existing_user_songs(
        self,
        sync_service: SyncService,
        mock_firestore: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Updates existing UserSong records."""
        # Mock existing user song
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": "user_123:123",
                "user_id": "user_123",
                "song_id": "123",
                "play_count": 5,
            }
        )

        result = await sync_service.sync_spotify("user_123", sample_spotify_service)

        # Should update existing
        assert result.user_songs_updated > 0

    @pytest.mark.asyncio
    async def test_sync_handles_token_error(
        self,
        sync_service: SyncService,
        mock_music_service_service: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Handles token refresh errors gracefully."""
        from backend.services.music_service_service import MusicServiceError

        mock_music_service_service.get_valid_spotify_token = AsyncMock(
            side_effect=MusicServiceError("Token refresh failed")
        )

        result = await sync_service.sync_spotify("user_123", sample_spotify_service)

        assert result.error is not None
        assert "Token refresh failed" in result.error

    @pytest.mark.asyncio
    async def test_sync_handles_api_error(
        self,
        sync_service: SyncService,
        mock_spotify_client: MagicMock,
        sample_spotify_service: MusicService,
    ) -> None:
        """Handles Spotify API errors gracefully."""
        mock_spotify_client.get_saved_tracks = AsyncMock(side_effect=ExternalServiceError("Spotify", "Rate limited"))

        result = await sync_service.sync_spotify("user_123", sample_spotify_service)

        assert result.error is not None
        assert "Rate limited" in result.error


class TestSyncLastFm:
    """Tests for sync_lastfm method."""

    @pytest.mark.asyncio
    async def test_successful_sync(
        self,
        sync_service: SyncService,
        mock_music_service_service: MagicMock,
        sample_lastfm_service: MusicService,
    ) -> None:
        """Successful Last.fm sync returns counts."""
        result = await sync_service.sync_lastfm("user_123", sample_lastfm_service)

        assert result.service_type == "lastfm"
        assert result.tracks_fetched > 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_sync_handles_api_error(
        self,
        sync_service: SyncService,
        mock_lastfm_client: MagicMock,
        sample_lastfm_service: MusicService,
    ) -> None:
        """Handles Last.fm API errors gracefully."""
        # Now uses get_all_top_tracks for paginated fetching
        mock_lastfm_client.get_all_top_tracks = AsyncMock(side_effect=ExternalServiceError("Last.fm", "User not found"))

        result = await sync_service.sync_lastfm("user_123", sample_lastfm_service)

        assert result.error is not None
        assert "User not found" in result.error


class TestFetchSpotifyTracks:
    """Tests for _fetch_spotify_tracks method."""

    @pytest.mark.asyncio
    async def test_fetches_saved_tracks(self, sync_service: SyncService, mock_spotify_client: MagicMock) -> None:
        """Fetches saved tracks from Spotify."""
        tracks = await sync_service._fetch_spotify_tracks("token")

        mock_spotify_client.get_saved_tracks.assert_called()
        assert any(t["title"] == "Bohemian Rhapsody" for t in tracks)

    @pytest.mark.asyncio
    async def test_fetches_top_tracks(self, sync_service: SyncService, mock_spotify_client: MagicMock) -> None:
        """Fetches top tracks from Spotify."""
        tracks = await sync_service._fetch_spotify_tracks("token")

        mock_spotify_client.get_top_tracks.assert_called()
        assert any(t["title"] == "Under Pressure" for t in tracks)

    @pytest.mark.asyncio
    async def test_fetches_recently_played(self, sync_service: SyncService, mock_spotify_client: MagicMock) -> None:
        """Fetches recently played from Spotify."""
        tracks = await sync_service._fetch_spotify_tracks("token")

        mock_spotify_client.get_recently_played.assert_called()
        assert any(t["title"] == "We Will Rock You" for t in tracks)

    @pytest.mark.asyncio
    async def test_deduplicates_tracks(self, sync_service: SyncService, mock_spotify_client: MagicMock) -> None:
        """Deduplicates tracks across sources."""
        # Mock same track appearing multiple times
        mock_spotify_client.get_saved_tracks = AsyncMock(
            return_value={
                "items": [
                    {
                        "track": {
                            "name": "Same Song",
                            "artists": [{"name": "Same Artist"}],
                        }
                    }
                ]
            }
        )
        mock_spotify_client.get_top_tracks = AsyncMock(
            return_value={
                "items": [
                    {
                        "name": "Same Song",
                        "artists": [{"name": "Same Artist"}],
                    }
                ]
            }
        )

        tracks = await sync_service._fetch_spotify_tracks("token")

        # Should only have one instance
        same_songs = [t for t in tracks if t["title"] == "Same Song"]
        assert len(same_songs) == 1


class TestFetchLastFmTracks:
    """Tests for _fetch_lastfm_tracks method."""

    @pytest.mark.asyncio
    async def test_fetches_top_tracks_with_playcount(
        self, sync_service: SyncService, mock_lastfm_client: MagicMock
    ) -> None:
        """Fetches top tracks from Last.fm with play counts via get_all_top_tracks."""
        tracks = await sync_service._fetch_lastfm_tracks("testuser")

        # Now uses get_all_top_tracks for paginated fetching with play counts
        mock_lastfm_client.get_all_top_tracks.assert_called()
        assert any(t["title"] == "Song 1" for t in tracks)
        # Should include play count data
        song1 = next(t for t in tracks if t["title"] == "Song 1")
        assert song1["playcount"] == 100

    @pytest.mark.asyncio
    async def test_fetches_loved_tracks(self, sync_service: SyncService, mock_lastfm_client: MagicMock) -> None:
        """Fetches loved tracks from Last.fm."""
        tracks = await sync_service._fetch_lastfm_tracks("testuser")

        mock_lastfm_client.get_loved_tracks.assert_called()
        assert any(t["title"] == "Loved Song" for t in tracks)

    @pytest.mark.asyncio
    async def test_includes_rank_info(self, sync_service: SyncService, mock_lastfm_client: MagicMock) -> None:
        """Includes rank information from top tracks."""
        tracks = await sync_service._fetch_lastfm_tracks("testuser")

        # Top tracks should include rank
        song1 = next(t for t in tracks if t["title"] == "Song 1")
        assert song1["rank"] == 1

    @pytest.mark.asyncio
    async def test_handles_artist_name_format(self, sync_service: SyncService, mock_lastfm_client: MagicMock) -> None:
        """Handles Last.fm artist dict format."""
        tracks = await sync_service._fetch_lastfm_tracks("testuser")

        # Top tracks use {"name": "..."} format
        assert any(t["artist"] == "Artist 1" for t in tracks)


class TestExtractTrackInfo:
    """Tests for track info extraction methods."""

    def test_extract_spotify_track_basic(self, sync_service: SyncService) -> None:
        """Extracts basic Spotify track info including metadata."""
        track = {
            "name": "Test Song",
            "artists": [{"name": "Test Artist"}],
        }

        result = sync_service._extract_spotify_track_info(track)

        assert result is not None
        assert result["artist"] == "Test Artist"
        assert result["title"] == "Test Song"
        # New fields with defaults
        assert result["popularity"] == 0
        assert result["duration_ms"] is None
        assert result["explicit"] is False

    def test_extract_spotify_track_multiple_artists(self, sync_service: SyncService) -> None:
        """Uses primary artist for multiple artists."""
        track = {
            "name": "Collaboration",
            "artists": [{"name": "Primary"}, {"name": "Featured"}],
        }

        result = sync_service._extract_spotify_track_info(track)

        assert result is not None
        assert result["artist"] == "Primary"

    def test_extract_spotify_track_empty(self, sync_service: SyncService) -> None:
        """Returns None for empty track."""
        assert sync_service._extract_spotify_track_info({}) is None
        assert sync_service._extract_spotify_track_info({"name": ""}) is None

    def test_extract_lastfm_track_basic(self, sync_service: SyncService) -> None:
        """Extracts basic Last.fm track info including playcount and rank."""
        track = {
            "name": "Test Song",
            "artist": {"name": "Test Artist"},
        }

        result = sync_service._extract_lastfm_track_info(track)

        assert result is not None
        assert result["artist"] == "Test Artist"
        assert result["title"] == "Test Song"
        # Default values for playcount and rank when not provided
        assert result["playcount"] is None
        assert result["rank"] is None

    def test_extract_lastfm_track_with_playcount(self, sync_service: SyncService) -> None:
        """Extracts Last.fm track info with playcount."""
        track = {
            "name": "Test Song",
            "artist": {"name": "Test Artist"},
            "playcount": "150",
            "rank": 5,
        }

        result = sync_service._extract_lastfm_track_info(track)

        assert result is not None
        assert result["playcount"] == 150
        assert result["rank"] == 5

    def test_extract_lastfm_track_text_format(self, sync_service: SyncService) -> None:
        """Handles Last.fm #text artist format."""
        track = {
            "name": "Test Song",
            "artist": {"#text": "Text Artist"},
        }

        result = sync_service._extract_lastfm_track_info(track)

        assert result is not None
        assert result["artist"] == "Text Artist"

    def test_extract_lastfm_track_empty(self, sync_service: SyncService) -> None:
        """Returns None for empty track."""
        assert sync_service._extract_lastfm_track_info({}) is None
        assert sync_service._extract_lastfm_track_info({"name": ""}) is None


class TestUpsertUserSongs:
    """Tests for _upsert_user_songs method."""

    @pytest.mark.asyncio
    async def test_creates_new_user_songs(self, sync_service: SyncService, mock_firestore: MagicMock) -> None:
        """Creates UserSong for new matched tracks."""
        mock_firestore.get_document = AsyncMock(return_value=None)

        mock_song = MagicMock()
        mock_song.id = "song_123"
        mock_song.artist = "Queen"
        mock_song.title = "Bohemian Rhapsody"

        matched = [
            MatchedTrack(
                original_artist="Queen",
                original_title="Bohemian Rhapsody",
                normalized_artist="queen",
                normalized_title="bohemian rhapsody",
                catalog_song=mock_song,
                match_confidence=1.0,
            )
        ]

        created, updated = await sync_service._upsert_user_songs("user_123", matched, "spotify")

        assert created == 1
        assert updated == 0
        mock_firestore.set_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_user_songs(self, sync_service: SyncService, mock_firestore: MagicMock) -> None:
        """Updates sync_count for existing UserSong."""
        mock_firestore.get_document = AsyncMock(
            return_value={
                "id": "user_123:song_123",
                "user_id": "user_123",
                "song_id": "song_123",
                "sync_count": 5,  # Now called sync_count instead of play_count
            }
        )

        mock_song = MagicMock()
        mock_song.id = "song_123"
        mock_song.artist = "Queen"
        mock_song.title = "Bohemian Rhapsody"

        matched = [
            MatchedTrack(
                original_artist="Queen",
                original_title="Bohemian Rhapsody",
                normalized_artist="queen",
                normalized_title="bohemian rhapsody",
                catalog_song=mock_song,
                match_confidence=1.0,
            )
        ]

        created, updated = await sync_service._upsert_user_songs("user_123", matched, "spotify")

        assert created == 0
        assert updated == 1

        # Check sync_count was incremented
        call_args = mock_firestore.update_document.call_args
        assert call_args[0][2]["sync_count"] == 6

    @pytest.mark.asyncio
    async def test_stores_unmatched_tracks_for_generate_feature(
        self, sync_service: SyncService, mock_firestore: MagicMock
    ) -> None:
        """Stores unmatched tracks with synthetic song_id for Generate Your Own feature."""
        matched = [
            MatchedTrack(
                original_artist="Unknown Artist",
                original_title="Unknown Song",
                normalized_artist="unknown artist",
                normalized_title="unknown song",
                catalog_song=None,
                match_confidence=0.0,
            )
        ]

        created, updated = await sync_service._upsert_user_songs("user_123", matched, "spotify")

        # Unmatched tracks are now stored with has_karaoke_version=False
        assert created == 1
        assert updated == 0

        # Verify the document was created with synthetic song_id
        call_args = mock_firestore.set_document.call_args
        doc_data = call_args[0][2]
        assert doc_data["song_id"] == "spotify:unknown artist:unknown song"
        assert doc_data["has_karaoke_version"] is False
        assert doc_data["artist"] == "Unknown Artist"
        assert doc_data["title"] == "Unknown Song"
