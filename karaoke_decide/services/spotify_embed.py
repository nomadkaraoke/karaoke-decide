"""Spotify public playlist reader via the embed page (best-effort).

``https://open.spotify.com/embed/playlist/{id}`` is server-rendered by Next.js and carries the first
~100 tracks of a public playlist in its ``__NEXT_DATA__`` script, with no auth. That markup is not a
supported API, so a layout change raises SpotifyEmbedFormatChangedError (the route answers 422) and a
daily canary (``.github/workflows/spotify-embed-canary.yml``) posts to #noise when parsing breaks.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EMBED_TRACK_LIMIT = 100
CANARY_MIN_TRACKS = 10
DEFAULT_EMBED_BASE = "https://open.spotify.com"

_ID_RE = re.compile(r"[A-Za-z0-9]{22}")
_URL_ID_RE = re.compile(r"open\.spotify\.com/(?:intl-[A-Za-z-]+/)?(?:embed/)?playlist/([A-Za-z0-9]{22})(?![A-Za-z0-9])")
_URI_ID_RE = re.compile(r"spotify:playlist:([A-Za-z0-9]{22})(?![A-Za-z0-9])")
_NEXT_DATA_RE = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class InvalidSpotifyPlaylistUrlError(ValueError):
    """The input is not a Spotify playlist link, URI or id."""


class SpotifyPlaylistNotFoundError(ValueError):
    """The playlist doesn't exist or is private."""


class SpotifyEmbedFetchError(RuntimeError):
    """Spotify could not be reached or answered with a server error."""


class SpotifyEmbedFormatChangedError(RuntimeError):
    """The embed page no longer has the expected ``__NEXT_DATA__`` shape."""


@dataclass
class SpotifyEmbedPlaylist:
    title: str
    tracks: list[dict[str, Any]] = field(default_factory=list)


def parse_spotify_playlist_id(value: str) -> str:
    """Extract a playlist id from a link, ``spotify:playlist:`` URI, bare id, or share text containing one."""
    text = (value or "").strip()
    for pattern in (_URL_ID_RE, _URI_ID_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    if _ID_RE.fullmatch(text):
        return text
    raise InvalidSpotifyPlaylistUrlError(f"No Spotify playlist id found in: {text[:80]}")


def _first_artist(subtitle: str) -> str:
    # Artists are joined by "," plus a non-breaking space; the first one is the primary artist.
    return subtitle.split(",")[0].replace("\xa0", " ").strip()


def parse_embed_html(html: str, limit: int = EMBED_TRACK_LIMIT) -> SpotifyEmbedPlaylist:
    """Read the playlist title and tracks from the embed page's ``__NEXT_DATA__``."""
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        raise SpotifyEmbedFormatChangedError("__NEXT_DATA__ script not found")
    try:
        entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        title = entity["name"]
        track_list = entity["trackList"]
    except (ValueError, KeyError, TypeError) as e:
        raise SpotifyEmbedFormatChangedError(f"expected path missing: {e!r}") from e
    if not isinstance(track_list, list) or not isinstance(title, str):
        raise SpotifyEmbedFormatChangedError("trackList/name have unexpected types")

    tracks: list[dict[str, Any]] = []
    for item in track_list:
        if not isinstance(item, dict):
            continue
        song, subtitle = item.get("title"), item.get("subtitle")
        if not isinstance(song, str) or not isinstance(subtitle, str):
            continue
        artist = _first_artist(subtitle)
        if artist and song.strip():
            tracks.append({"artist": artist, "title": song.strip()})
    return SpotifyEmbedPlaylist(title=title, tracks=tracks[:limit])


class SpotifyEmbedClient:
    TIMEOUT_SECONDS = 10.0

    def __init__(self, base: str = DEFAULT_EMBED_BASE):
        self.base = base.rstrip("/")

    async def get_playlist(self, playlist_id: str, limit: int = EMBED_TRACK_LIMIT) -> SpotifyEmbedPlaylist:
        """Fetch a public playlist. Raises SpotifyPlaylistNotFoundError, SpotifyEmbedFetchError,
        SpotifyEmbedFormatChangedError."""
        url = f"{self.base}/embed/playlist/{playlist_id}"
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS, follow_redirects=True) as http:
                response = await http.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; NomadKaraoke/1.0)"})
        except httpx.HTTPError as e:
            raise SpotifyEmbedFetchError(str(e)) from e
        if response.status_code in (400, 403, 404):
            raise SpotifyPlaylistNotFoundError(playlist_id)
        if response.status_code >= 400:
            raise SpotifyEmbedFetchError(f"HTTP {response.status_code}")
        return parse_embed_html(response.text, limit=min(limit, EMBED_TRACK_LIMIT))


async def _canary(playlist_id: str, base: str) -> int:
    try:
        playlist = await SpotifyEmbedClient(base).get_playlist(playlist_id)
    except Exception as e:  # any failure is a canary failure; the reason goes to the Discord message
        print(f"{type(e).__name__}: {e}")
        return 1
    if len(playlist.tracks) < CANARY_MIN_TRACKS:
        print(f"only {len(playlist.tracks)} tracks parsed (expected at least {CANARY_MIN_TRACKS})")
        return 1
    print(f"ok: {len(playlist.tracks)} tracks parsed from {playlist.title!r}")
    return 0


def main(argv: list[str]) -> int:
    import asyncio

    if len(argv) != 3 or argv[1] != "canary":
        print("usage: python -m karaoke_decide.services.spotify_embed canary <playlist_id>")
        return 2
    return asyncio.run(_canary(argv[2], DEFAULT_EMBED_BASE))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
