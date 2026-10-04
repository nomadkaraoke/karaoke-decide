"""Tests for the YouTube Music playlist reader."""

from unittest.mock import MagicMock

import pytest
import requests

from karaoke_decide.services.youtube_music import (
    InvalidPlaylistUrlError,
    PlaylistFetchError,
    PlaylistNotFoundError,
    PrivatePlaylistError,
    YouTubeMusicClient,
    clean_video_title,
    parse_playlist_id,
)

PID = "PLabcDEF123_-xyz"


class TestParsePlaylistId:
    @pytest.mark.parametrize(
        "value",
        [
            f"https://music.youtube.com/playlist?list={PID}",
            f"https://music.youtube.com/playlist?list={PID}&si=share123",
            f"https://www.youtube.com/playlist?list={PID}",
            f"https://m.youtube.com/playlist?list={PID}",
            f"https://www.youtube.com/watch?v=vid123&list={PID}",
            f"https://youtu.be/vid123?list={PID}",
            f"music.youtube.com/playlist?list={PID}",
            f"https://music.youtube.com/browse/VL{PID}",
            f"  {PID}  ",
            f"VL{PID}",
            f"youtu.be/vid123?list={PID}",
        ],
    )
    def test_extracts_id(self, value: str) -> None:
        assert parse_playlist_id(value) == PID

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "https://example.com/playlist?list=PLabc",
            "https://evilyoutube.com/playlist?list=PLabc",
            "https://music.youtube.com/watch?v=vid123",
            "not a link at all",
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(InvalidPlaylistUrlError):
            parse_playlist_id(value)

    @pytest.mark.parametrize("pid", ["LM", "LL", "WL"])
    def test_rejects_private_auto_playlists(self, pid: str) -> None:
        with pytest.raises(PrivatePlaylistError):
            parse_playlist_id(f"https://music.youtube.com/playlist?list={pid}")

    @pytest.mark.parametrize("value", ["LM", "VLLM", "https://music.youtube.com/browse/VLLM"])
    def test_rejects_private_auto_playlist_ids(self, value: str) -> None:
        with pytest.raises(PrivatePlaylistError):
            parse_playlist_id(value)


class TestCleanVideoTitle:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Sample Tune (Official Video)", "Sample Tune"),
            ("Sample Tune (Official Music Video)", "Sample Tune"),
            ("Sample Tune [Official Audio]", "Sample Tune"),
            ("Sample Tune (Lyric Video)", "Sample Tune"),
            ("Sample Tune (Lyrics)", "Sample Tune"),
            ("Sample Tune (HD)", "Sample Tune"),
            ("Sample Tune Video", "Sample Tune"),
            ("Sample Tune - Official Video", "Sample Tune"),
            ("Sample Tune | Official Audio", "Sample Tune"),
            ("Sample Tune (Live Version) | Official Video", "Sample Tune (Live Version)"),
            ("Sample Tune - Remix", "Sample Tune - Remix"),
            ("Sample Tune (Part Two)", "Sample Tune (Part Two)"),
        ],
    )
    def test_strips_video_noise(self, title: str, expected: str) -> None:
        assert clean_video_title(title, "MUSIC_VIDEO_TYPE_OMV") == expected

    def test_keeps_trailing_video_for_song_tracks(self) -> None:
        assert clean_video_title("Placeholder Video", "MUSIC_VIDEO_TYPE_ATV") == "Placeholder Video"

    def test_never_returns_empty(self) -> None:
        assert clean_video_title("Video", "MUSIC_VIDEO_TYPE_OMV") == "Video"


class TestGetPlaylist:
    @pytest.mark.asyncio
    async def test_maps_tracks(self) -> None:
        ytmusic = MagicMock()
        ytmusic.get_playlist.return_value = {
            "title": "My Likes",
            "tracks": [
                {
                    "title": "First Tune (Official Video)",
                    "artists": [{"name": "Band One"}, {"name": "Guest"}],
                    "duration_seconds": 200,
                    "videoType": "MUSIC_VIDEO_TYPE_OMV",
                    "isExplicit": True,
                },
                {"title": "Second Tune", "artists": [{"name": "Band Two"}], "videoType": "MUSIC_VIDEO_TYPE_ATV"},
                {"title": "No Artist", "artists": []},
                {"title": None, "artists": [{"name": "Band Three"}]},
            ],
        }

        playlist = await YouTubeMusicClient(ytmusic).get_playlist(PID, limit=50)

        ytmusic.get_playlist.assert_called_once_with(PID, limit=50)
        assert playlist.title == "My Likes"
        assert playlist.tracks == [
            {"artist": "Band One", "title": "First Tune", "duration_ms": 200000, "explicit": True},
            {"artist": "Band Two", "title": "Second Tune", "duration_ms": None, "explicit": False},
        ]

    @pytest.mark.asyncio
    async def test_respects_limit(self) -> None:
        ytmusic = MagicMock()
        ytmusic.get_playlist.return_value = {
            "title": "Big",
            "tracks": [{"title": f"Tune {i}", "artists": [{"name": "Band"}]} for i in range(10)],
        }
        playlist = await YouTubeMusicClient(ytmusic).get_playlist(PID, limit=3)
        assert len(playlist.tracks) == 3

    @pytest.mark.asyncio
    async def test_missing_playlist_raises_not_found(self, caplog: pytest.LogCaptureFixture) -> None:
        ytmusic = MagicMock()
        ytmusic.get_playlist.side_effect = KeyError("contents")
        with pytest.raises(PlaylistNotFoundError):
            await YouTubeMusicClient(ytmusic).get_playlist(PID, limit=10)
        assert "not readable" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [requests.ConnectionError("down"), Exception("Server returned HTTP 429")])
    async def test_upstream_failure_raises_fetch_error(self, error: Exception) -> None:
        ytmusic = MagicMock()
        ytmusic.get_playlist.side_effect = error
        with pytest.raises(PlaylistFetchError):
            await YouTubeMusicClient(ytmusic).get_playlist(PID, limit=10)
