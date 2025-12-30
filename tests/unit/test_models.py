"""Tests for core data models."""

from datetime import datetime

from karaoke_decide.core.models import (
    KaraokeSong,
    Playlist,
    SongSource,
    User,
    UserSong,
)


class TestUser:
    """Tests for User model."""

    def test_create_user(self) -> None:
        user = User(
            id="user123",
            email="test@example.com",
            display_name="Test User",
        )
        assert user.id == "user123"
        assert user.email == "test@example.com"
        assert user.display_name == "Test User"
        assert user.total_songs_known == 0
        assert user.total_songs_sung == 0

    def test_user_defaults(self) -> None:
        user = User(id="user123", email="test@example.com")
        assert user.display_name is None
        assert isinstance(user.created_at, datetime)


class TestKaraokeSong:
    """Tests for KaraokeSong model."""

    def test_create_song(self) -> None:
        song = KaraokeSong(
            id="queen-bohemian-rhapsody",
            artist="Queen",
            title="Bohemian Rhapsody",
            sources=[SongSource(source="karaokenerds", external_id="12345")],
        )
        assert song.id == "queen-bohemian-rhapsody"
        assert song.artist == "Queen"
        assert len(song.sources) == 1
        assert song.sources[0].source == "karaokenerds"

    def test_song_defaults(self) -> None:
        song = KaraokeSong(
            id="test",
            artist="Test",
            title="Test",
        )
        assert song.sources == []
        assert song.genres == []
        assert song.is_popular_karaoke is False


class TestUserSong:
    """Tests for UserSong model."""

    def test_create_user_song(self) -> None:
        user_song = UserSong(
            id="user123:queen-bohemian-rhapsody",
            user_id="user123",
            song_id="queen-bohemian-rhapsody",
            artist="Queen",
            title="Bohemian Rhapsody",
            play_count=50,
        )
        assert user_song.play_count == 50
        assert user_song.times_sung == 0


class TestPlaylist:
    """Tests for Playlist model."""

    def test_create_playlist(self) -> None:
        playlist = Playlist(
            id="playlist123",
            user_id="user123",
            name="Friday Night",
            song_ids=["song1", "song2", "song3"],
        )
        assert playlist.name == "Friday Night"
        assert len(playlist.song_ids) == 3
        assert playlist.song_count == 0  # Must be set manually

    def test_playlist_defaults(self) -> None:
        playlist = Playlist(
            id="playlist123",
            user_id="user123",
            name="Test",
        )
        assert playlist.description is None
        assert playlist.song_ids == []
