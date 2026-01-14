"""ListenBrainz API client for user listening data.

Fetches user listening history from ListenBrainz, which can import data from
Spotify, Apple Music, and other services via their "Import Data" feature.

This is separate from backend/services/listenbrainz_service.py which handles
artist similarity lookups.
"""

import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass

import httpx

from backend.config import BackendSettings
from karaoke_decide.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


@dataclass
class ListenBrainzUserInfo:
    """User information from ListenBrainz."""

    username: str
    listen_count: int


@dataclass
class ListenBrainzListen:
    """A single listen from ListenBrainz."""

    artist_name: str
    track_name: str
    listened_at: int  # Unix timestamp
    recording_mbid: str | None = None
    release_name: str | None = None
    artist_mbids: list[str] | None = None


class ListenBrainzClient:
    """API client for ListenBrainz user data.

    ListenBrainz is an open-source music tracking service that allows users
    to import their listening history from Spotify, Apple Music, and other
    services. No API key required for public user data.

    API Docs: https://listenbrainz.readthedocs.io/en/latest/users/api/index.html
    """

    API_BASE = "https://api.listenbrainz.org/1"

    def __init__(self, settings: BackendSettings | None = None):
        """Initialize the ListenBrainz client.

        Args:
            settings: Backend settings (not required for ListenBrainz as it's public API).
        """
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "KaraokeDecide/1.0 (contact@nomadkaraoke.com)"},
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_user_listen_count(self, username: str) -> int:
        """Get total listen count for a user.

        This also validates that the username exists.

        Args:
            username: ListenBrainz username.

        Returns:
            Total listen count.

        Raises:
            ExternalServiceError: If user not found or API error.
        """
        client = await self._get_client()

        try:
            response = await client.get(f"{self.API_BASE}/user/{username}/listen-count")

            if response.status_code == 404:
                raise ExternalServiceError("ListenBrainz", f"User '{username}' not found")

            if response.status_code != 200:
                raise ExternalServiceError("ListenBrainz", f"API error: {response.status_code}")

            data = response.json()
            count: int = data.get("payload", {}).get("count", 0)
            return count

        except httpx.HTTPError as e:
            raise ExternalServiceError("ListenBrainz", f"Failed to connect: {e}")

    async def get_user_info(self, username: str) -> ListenBrainzUserInfo:
        """Get user information.

        Args:
            username: ListenBrainz username.

        Returns:
            User info with username and listen count.

        Raises:
            ExternalServiceError: If user not found or API error.
        """
        listen_count = await self.get_user_listen_count(username)
        return ListenBrainzUserInfo(username=username, listen_count=listen_count)

    async def get_listens(
        self,
        username: str,
        count: int = 100,
        max_ts: int | None = None,
        min_ts: int | None = None,
    ) -> list[ListenBrainzListen]:
        """Get user's recent listens.

        Args:
            username: ListenBrainz username.
            count: Number of listens to fetch (max 100).
            max_ts: Only return listens before this timestamp.
            min_ts: Only return listens after this timestamp.

        Returns:
            List of listens.

        Raises:
            ExternalServiceError: If API error.
        """
        client = await self._get_client()

        params: dict = {"count": min(count, 100)}
        if max_ts is not None:
            params["max_ts"] = max_ts
        if min_ts is not None:
            params["min_ts"] = min_ts

        try:
            response = await client.get(
                f"{self.API_BASE}/user/{username}/listens",
                params=params,
            )

            if response.status_code != 200:
                raise ExternalServiceError("ListenBrainz", f"API error: {response.status_code}")

            data = response.json()
            listens_data = data.get("payload", {}).get("listens", [])

            listens = []
            for listen in listens_data:
                track_metadata = listen.get("track_metadata", {})
                additional_info = track_metadata.get("additional_info", {})

                listens.append(
                    ListenBrainzListen(
                        artist_name=track_metadata.get("artist_name", "Unknown Artist"),
                        track_name=track_metadata.get("track_name", "Unknown Track"),
                        listened_at=listen.get("listened_at", 0),
                        recording_mbid=track_metadata.get("mbid_mapping", {}).get("recording_mbid"),
                        release_name=track_metadata.get("release_name"),
                        artist_mbids=additional_info.get("artist_mbids"),
                    )
                )

            return listens

        except httpx.HTTPError as e:
            raise ExternalServiceError("ListenBrainz", f"Failed to fetch listens: {e}")

    async def get_all_listens(
        self,
        username: str,
        max_ts: int | None = None,
        min_ts: int | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> AsyncGenerator[ListenBrainzListen, None]:
        """Get all listens for a user via pagination.

        Yields listens from newest to oldest.

        Args:
            username: ListenBrainz username.
            max_ts: Start from this timestamp (inclusive).
            min_ts: Stop at this timestamp (inclusive).
            progress_callback: Optional callback(total, processed).

        Yields:
            ListenBrainzListen objects.
        """
        current_max_ts = max_ts
        total_fetched = 0
        total_count = await self.get_user_listen_count(username)

        while True:
            listens = await self.get_listens(
                username,
                count=100,
                max_ts=current_max_ts,
                min_ts=min_ts,
            )

            if not listens:
                break

            for listen in listens:
                total_fetched += 1
                yield listen

                if progress_callback:
                    progress_callback(total_count, total_fetched)

            # Get timestamp for next page
            oldest_ts = listens[-1].listened_at
            current_max_ts = oldest_ts - 1

            # Check if we've reached min_ts
            if min_ts and oldest_ts <= min_ts:
                break

    async def get_top_artists(
        self,
        username: str,
        count: int = 100,
        range_: str = "all_time",
    ) -> list[dict]:
        """Get user's top artists.

        Args:
            username: ListenBrainz username.
            count: Number of artists to fetch (max 100).
            range_: Time range - "week", "month", "quarter", "half_yearly", "year", "all_time".

        Returns:
            List of artist dicts with artist_name, listen_count, artist_mbid.

        Raises:
            ExternalServiceError: If API error.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.API_BASE}/stats/user/{username}/artists",
                params={"count": min(count, 100), "range": range_},
            )

            if response.status_code != 200:
                raise ExternalServiceError("ListenBrainz", f"API error: {response.status_code}")

            data = response.json()
            artists_data = data.get("payload", {}).get("artists", [])

            return [
                {
                    "artist_name": a.get("artist_name", "Unknown"),
                    "listen_count": a.get("listen_count", 0),
                    "artist_mbid": a.get("artist_mbid"),
                }
                for a in artists_data
            ]

        except httpx.HTTPError as e:
            raise ExternalServiceError("ListenBrainz", f"Failed to fetch top artists: {e}")

    async def get_top_tracks(
        self,
        username: str,
        count: int = 100,
        range_: str = "all_time",
    ) -> list[dict]:
        """Get user's top tracks.

        Args:
            username: ListenBrainz username.
            count: Number of tracks to fetch (max 100).
            range_: Time range - "week", "month", "quarter", "half_yearly", "year", "all_time".

        Returns:
            List of track dicts with track_name, artist_name, listen_count, recording_mbid.

        Raises:
            ExternalServiceError: If API error.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.API_BASE}/stats/user/{username}/recordings",
                params={"count": min(count, 100), "range": range_},
            )

            if response.status_code != 200:
                raise ExternalServiceError("ListenBrainz", f"API error: {response.status_code}")

            data = response.json()
            tracks_data = data.get("payload", {}).get("recordings", [])

            return [
                {
                    "track_name": t.get("track_name", "Unknown"),
                    "artist_name": t.get("artist_name", "Unknown"),
                    "listen_count": t.get("listen_count", 0),
                    "recording_mbid": t.get("recording_mbid"),
                }
                for t in tracks_data
            ]

        except httpx.HTTPError as e:
            raise ExternalServiceError("ListenBrainz", f"Failed to fetch top tracks: {e}")
