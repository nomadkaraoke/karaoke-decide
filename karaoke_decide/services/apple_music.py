"""Apple Music API client (MusicKit) for a user's library and recent plays.

Two tokens are involved:
- The developer token: an ES256 JWT we sign with our MusicKit key. It identifies Nomad Karaoke to Apple.
  Web pages get an origin-restricted one (MusicKit JS needs it before the user signs in).
- The Music User Token: what MusicKit JS returns after the user signs in to Apple Music. It lasts about
  six months and is sent as the `Music-User-Token` header on every /v1/me request.

API docs: https://developer.apple.com/documentation/applemusicapi
"""

import logging
import time
from dataclasses import dataclass, field

import httpx

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

API_BASE = "https://api.music.apple.com/v1"

# Apple allows up to 15,777,000 s (6 months). We sign shorter tokens and re-sign before they expire.
DEVELOPER_TOKEN_TTL_SECONDS = 30 * 24 * 3600
_REFRESH_MARGIN_SECONDS = 24 * 3600

LIBRARY_PAGE_SIZE = 100  # Apple's maximum for library resources
RECENT_PAGE_SIZE = 30  # Apple's maximum for recently played tracks
MAX_LIBRARY_SONGS = 3000
MAX_RECENT_TRACKS = 150


class AppleMusicNotConfiguredError(Exception):
    """The MusicKit key, key id or team id isn't set."""


class AppleMusicAuthError(ExternalServiceError):
    """Apple refused the Music User Token (expired, revoked, or no Apple Music subscription)."""

    def __init__(self, message: str):
        super().__init__("Apple Music", message)


@dataclass
class AppleMusicTrack:
    """A song from the user's library or play history."""

    artist: str
    title: str
    duration_ms: int | None = None


@dataclass
class AppleMusicLibrary:
    """What one sync reads from a user's Apple Music account."""

    recent: list[AppleMusicTrack] = field(default_factory=list)
    library: list[AppleMusicTrack] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)


# Signed tokens per (key id, origins). Module-level: services are rebuilt per request.
_token_cache: dict[tuple[str, tuple[str, ...]], tuple[str, int]] = {}


class DeveloperTokenSigner:
    """Signs and caches developer tokens (one per origin set)."""

    def __init__(self, settings: Settings):
        self._key = settings.apple_musickit_private_key.replace("\\n", "\n").strip()
        self._key_id = settings.apple_musickit_key_id.strip()
        self._team_id = settings.apple_team_id.strip()

    @property
    def configured(self) -> bool:
        return bool(self._key and self._key_id and self._team_id)

    def token(self, origins: tuple[str, ...] = ()) -> tuple[str, int]:
        """Return (token, expires_at unix seconds). `origins` restricts where a web page may use it."""
        if not self.configured:
            raise AppleMusicNotConfiguredError("Apple MusicKit key is not configured")
        now = int(time.time())
        cached = _token_cache.get((self._key_id, origins))
        if cached and cached[1] - now > _REFRESH_MARGIN_SECONDS:
            return cached
        expires_at = now + DEVELOPER_TOKEN_TTL_SECONDS
        claims: dict[str, object] = {"iss": self._team_id, "iat": now, "exp": expires_at}
        if origins:
            claims["origin"] = list(origins)
        from jose import jwt  # lazy: jose pulls in cryptography, kept off the cold-start path

        signed = jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self._key_id})
        _token_cache[(self._key_id, origins)] = (signed, expires_at)
        return signed, expires_at


class AppleMusicClient:
    """Reads a user's Apple Music library with their Music User Token."""

    def __init__(self, signer: DeveloperTokenSigner, http: httpx.AsyncClient | None = None):
        self._signer = signer
        self._http = http

    async def _get(self, path: str, user_token: str, params: dict[str, str | int] | None = None) -> dict:
        developer_token, _ = self._signer.token()
        headers = {"Authorization": f"Bearer {developer_token}", "Music-User-Token": user_token}
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.get(f"{API_BASE}{path}", headers=headers, params=params)
        except httpx.HTTPError as e:
            raise ExternalServiceError("Apple Music", f"Failed to connect: {e}")
        finally:
            if self._http is None:
                await client.aclose()
        if response.status_code in (401, 403):
            raise AppleMusicAuthError(f"Apple Music refused the request ({response.status_code})")
        if response.status_code == 404:
            return {"data": []}  # Apple answers 404 for an empty library or history
        if response.status_code != 200:
            raise ExternalServiceError("Apple Music", f"API error: {response.status_code}")
        body: dict = response.json()
        return body

    async def get_storefront(self, user_token: str) -> str:
        """The user's storefront (country), e.g. "us". Also proves the token works."""
        data = (await self._get("/me/storefront", user_token)).get("data") or []
        return str(data[0].get("id", "")) if data else ""

    async def _paged_songs(self, path: str, user_token: str, page_size: int, limit: int) -> list[AppleMusicTrack]:
        tracks: list[AppleMusicTrack] = []
        offset = 0
        while len(tracks) < limit:
            body = await self._get(path, user_token, {"limit": page_size, "offset": offset})
            items = body.get("data") or []
            tracks.extend(t for t in (_to_track(item) for item in items) if t is not None)
            # Follow Apple's `next` link: pages can come back short with more still to read.
            if not items or not body.get("next"):
                break
            offset += page_size
        return tracks[:limit]

    async def get_recent_tracks(self, user_token: str, limit: int = MAX_RECENT_TRACKS) -> list[AppleMusicTrack]:
        """Recently played songs, newest first."""
        return await self._paged_songs("/me/recent/played/tracks", user_token, RECENT_PAGE_SIZE, limit)

    async def get_library_songs(self, user_token: str, limit: int = MAX_LIBRARY_SONGS) -> list[AppleMusicTrack]:
        """Songs the user added to their library."""
        return await self._paged_songs("/me/library/songs", user_token, LIBRARY_PAGE_SIZE, limit)

    async def get_library(self, user_token: str) -> AppleMusicLibrary:
        """Recent plays plus library songs, and artists ranked by how often they appear."""
        recent = await self.get_recent_tracks(user_token)
        library = await self.get_library_songs(user_token)
        counts: dict[str, int] = {}
        for track in recent + recent + library:  # a recent play weighs twice a library song
            counts[track.artist] = counts.get(track.artist, 0) + 1
        artists = sorted(counts, key=lambda a: -counts[a])
        return AppleMusicLibrary(recent=recent, library=library, artists=artists)


def _to_track(item: dict) -> AppleMusicTrack | None:
    """A library song or catalog song resource → track; None for other types (music videos, stations)."""
    if item.get("type") not in ("songs", "library-songs"):
        return None
    attrs = item.get("attributes") or {}
    artist = (attrs.get("artistName") or "").strip()
    title = (attrs.get("name") or "").strip()
    if not artist or not title:
        return None
    duration = attrs.get("durationInMillis")
    return AppleMusicTrack(artist=artist, title=title, duration_ms=int(duration) if duration else None)
