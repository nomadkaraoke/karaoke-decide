"""YouTube Music playlist reader.

Reads public or unlisted YouTube Music / YouTube playlists without user auth,
so users can share e.g. a copy of their "Liked Music" playlist by link.

Note: the auto-generated "Liked Music" (LM) and "Liked videos" (LL) playlists
are always private and cannot be read this way - users must copy their likes
into a regular playlist first.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

# Private auto-generated playlists that are only visible to their owner
PRIVATE_PLAYLIST_IDS = {"LM", "LL", "WL"}

# Valid playlist ID characters (PL..., OLAK5uy_..., RDCLAK..., etc.)
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")

# Video-title noise that YouTube music videos carry but catalog titles don't,
# e.g. "(Official Video)", "[Lyrics]", "- Official Video", "| Official Audio"
_TITLE_NOISE_PATTERNS = [
    re.compile(
        r"\s*[\(\[][^\)\]]*\b(official|lyrics?|lyric video|audio|music video|video|visualizer|hd|hq|4k|remastered)\b"
        r"[^\)\]]*[\)\]]",
        re.IGNORECASE,
    ),
    re.compile(r"\s*[-|:\u2013\u2014]\s*official\s+(music\s+)?(video|audio|lyric video|visualizer)\s*$", re.IGNORECASE),
]
# Trailing bare " Video" suffix (e.g. "Some Song Video"), common on YouTube
# Music's official-video titles. Only stripped for videos, never song tracks.
# Trade-off: a music video for a song literally titled "... Video" loses the word.
_TRAILING_VIDEO_PATTERN = re.compile(r"\s+(official\s+)?(music\s+)?video$", re.IGNORECASE)
_TRAILING_SEPARATORS = " -|:\u2013\u2014"

# Song tracks ("Art Tracks") carry clean titles; everything else is a video
SONG_VIDEO_TYPE = "MUSIC_VIDEO_TYPE_ATV"


class InvalidPlaylistUrlError(ValueError):
    """The input could not be parsed as a YouTube playlist link or ID."""


class PrivatePlaylistError(ValueError):
    """The playlist is one of the always-private auto playlists (e.g. Liked Music)."""


class PlaylistNotFoundError(ValueError):
    """The playlist doesn't exist or is private."""


class PlaylistFetchError(RuntimeError):
    """YouTube could not be reached or returned an unexpected response."""


@dataclass
class YouTubeMusicPlaylist:
    """A fetched playlist."""

    playlist_id: str
    title: str
    tracks: list[dict[str, Any]] = field(default_factory=list)


def parse_playlist_id(value: str) -> str:
    """Extract a playlist ID from a YouTube / YouTube Music link or bare ID.

    Supports:
        https://music.youtube.com/playlist?list=PL...&si=...
        https://www.youtube.com/playlist?list=PL...
        https://www.youtube.com/watch?v=...&list=PL...
        https://music.youtube.com/browse/VLPL...
        PL... (bare ID)

    Raises:
        InvalidPlaylistUrlError: If no playlist ID can be found.
        PrivatePlaylistError: If the ID is an always-private auto playlist.
    """
    value = (value or "").strip()
    if not value:
        raise InvalidPlaylistUrlError("Empty playlist link")

    playlist_id: str | None = None

    if "://" in value or value.startswith(
        ("music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be")
    ):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower()
        if not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
            raise InvalidPlaylistUrlError(f"Not a YouTube link: {host}")

        list_param = parse_qs(parsed.query).get("list")
        if list_param:
            playlist_id = list_param[0]
        else:
            match = re.search(r"/browse/VL([A-Za-z0-9_-]+)", parsed.path)
            if match:
                playlist_id = match.group(1)
    else:
        playlist_id = value[2:] if value.startswith("VL") and len(value) > 2 else value

    if not playlist_id or not _PLAYLIST_ID_RE.match(playlist_id):
        raise InvalidPlaylistUrlError(f"No playlist ID found in: {value}")

    if playlist_id in PRIVATE_PLAYLIST_IDS:
        raise PrivatePlaylistError(playlist_id)

    return playlist_id


def clean_video_title(title: str, video_type: str | None = None) -> str:
    """Strip music-video noise like "(Official Video)" from a track title."""
    result = title or ""
    for pattern in _TITLE_NOISE_PATTERNS:
        result = pattern.sub("", result)
    if video_type != SONG_VIDEO_TYPE:
        result = _TRAILING_VIDEO_PATTERN.sub("", result)
    return result.strip(_TRAILING_SEPARATORS) or (title or "").strip()


class YouTubeMusicClient:
    """Unauthenticated reader for public/unlisted YouTube Music playlists."""

    def __init__(self, ytmusic: YTMusic | None = None):
        self._ytmusic = ytmusic

    @property
    def ytmusic(self) -> YTMusic:
        if self._ytmusic is None:
            self._ytmusic = YTMusic()
        return self._ytmusic

    async def get_playlist(self, playlist_id: str, limit: int) -> YouTubeMusicPlaylist:
        """Fetch a playlist's tracks.

        Args:
            playlist_id: Playlist ID (from parse_playlist_id).
            limit: Max number of tracks to fetch.

        Raises:
            PlaylistNotFoundError: Playlist doesn't exist or isn't public/unlisted.
            PlaylistFetchError: Network or unexpected upstream failure.
        """
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, lambda: self.ytmusic.get_playlist(playlist_id, limit=limit))
        except (KeyError, IndexError) as e:
            # ytmusicapi raises KeyError when the page has no playlist contents,
            # which is what YouTube returns for missing and private playlists.
            # Logged because an upstream page-format change looks identical.
            logger.warning(f"YouTube Music playlist {playlist_id} not readable: {e!s:.200}")
            raise PlaylistNotFoundError(playlist_id) from e
        except Exception as e:  # network errors (requests) and ytmusicapi's bare Exception on HTTP errors
            raise PlaylistFetchError(str(e)) from e

        tracks: list[dict[str, Any]] = []
        for item in (raw.get("tracks") or [])[:limit]:
            artists = [a.get("name") for a in (item.get("artists") or []) if a.get("name")]
            title = item.get("title")
            if not artists or not title:
                continue
            duration = item.get("duration_seconds")
            tracks.append(
                {
                    "artist": artists[0],
                    "title": clean_video_title(title, item.get("videoType")),
                    "duration_ms": duration * 1000 if isinstance(duration, int) else None,
                    "explicit": bool(item.get("isExplicit", False)),
                }
            )

        return YouTubeMusicPlaylist(
            playlist_id=playlist_id,
            title=raw.get("title") or "",
            tracks=tracks,
        )
