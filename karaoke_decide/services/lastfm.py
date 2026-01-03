"""Last.fm API client for Karaoke Decide."""

import hashlib
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import httpx

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


@dataclass
class ScrobbleProgress:
    """Progress information for scrobble fetching."""

    total_scrobbles: int
    fetched_scrobbles: int
    current_page: int
    total_pages: int

    @property
    def percentage(self) -> int:
        """Return progress percentage."""
        if self.total_scrobbles == 0:
            return 0
        return min(100, int((self.fetched_scrobbles / self.total_scrobbles) * 100))


# Type alias for progress callback
ProgressCallback = Callable[[ScrobbleProgress], Coroutine[Any, Any, None]]


class LastFmClient:
    """Client for Last.fm API."""

    API_BASE = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.lastfm_api_key
        self.shared_secret = settings.lastfm_shared_secret

    async def get_user_info(self, username: str) -> dict[str, Any]:
        """Get user profile information."""
        return await self._api_request(
            "user.getinfo",
            {"user": username},
        )

    async def get_loved_tracks(self, username: str, limit: int = 50, page: int = 1) -> dict[str, Any]:
        """Get user's loved tracks."""
        return await self._api_request(
            "user.getlovedtracks",
            {"user": username, "limit": limit, "page": page},
        )

    async def get_top_tracks(
        self,
        username: str,
        period: str = "overall",
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Get user's top tracks.

        Args:
            username: Last.fm username
            period: overall | 7day | 1month | 3month | 6month | 12month
            limit: Number of results per page
            page: Page number
        """
        return await self._api_request(
            "user.gettoptracks",
            {"user": username, "period": period, "limit": limit, "page": page},
        )

    async def get_top_artists(
        self,
        username: str,
        period: str = "overall",
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        """Get user's top artists with play counts.

        Args:
            username: Last.fm username
            period: overall | 7day | 1month | 3month | 6month | 12month
            limit: Number of results per page (max 1000)
            page: Page number

        Returns:
            dict with 'topartists' containing artist list with name, playcount, url
        """
        return await self._api_request(
            "user.gettopartists",
            {"user": username, "period": period, "limit": limit, "page": page},
        )

    async def get_recent_tracks(
        self,
        username: str,
        limit: int = 50,
        page: int = 1,
        from_timestamp: int | None = None,
        to_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Get user's recent tracks with play timestamps.

        Args:
            username: Last.fm username
            limit: Number of results per page (max 200)
            page: Page number
            from_timestamp: UNIX timestamp - only fetch scrobbles after this time
            to_timestamp: UNIX timestamp - only fetch scrobbles before this time

        Returns:
            dict with 'recenttracks' containing track list and pagination @attr
        """
        params: dict[str, Any] = {
            "user": username,
            "limit": limit,
            "page": page,
            "extended": 1,
        }
        if from_timestamp is not None:
            params["from"] = from_timestamp
        if to_timestamp is not None:
            params["to"] = to_timestamp

        return await self._api_request("user.getrecenttracks", params)

    async def get_all_scrobbles(
        self,
        username: str,
        from_timestamp: int | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Fetch ALL scrobbles for a user with pagination.

        This method fetches the complete scrobble history for a user,
        which can be 100k+ tracks spanning many years. Use progress_callback
        to track progress for long-running syncs.

        Args:
            username: Last.fm username
            from_timestamp: Optional UNIX timestamp to fetch scrobbles after
                           (for incremental sync of new scrobbles only)
            progress_callback: Optional async callback for progress updates

        Yields:
            Track dicts with artist, name (title), album, date info
        """
        page = 1
        total_pages = 1
        total_scrobbles = 0
        fetched_count = 0

        while page <= total_pages:
            response = await self.get_recent_tracks(
                username=username,
                limit=200,  # Max per page
                page=page,
                from_timestamp=from_timestamp,
            )

            tracks_data = response.get("recenttracks", {})
            tracks = tracks_data.get("track", [])

            # Get pagination info from @attr
            attr = tracks_data.get("@attr", {})
            total_pages = int(attr.get("totalPages", 1))
            total_scrobbles = int(attr.get("total", 0))

            # Report progress
            if progress_callback:
                progress = ScrobbleProgress(
                    total_scrobbles=total_scrobbles,
                    fetched_scrobbles=fetched_count,
                    current_page=page,
                    total_pages=total_pages,
                )
                await progress_callback(progress)

            logger.info(f"Last.fm scrobbles: page {page}/{total_pages}, " f"fetched {fetched_count}/{total_scrobbles}")

            for track in tracks:
                # Skip currently playing track (has @attr with nowplaying)
                if track.get("@attr", {}).get("nowplaying"):
                    continue
                fetched_count += 1
                yield track

            page += 1

        # Final progress update
        if progress_callback:
            progress = ScrobbleProgress(
                total_scrobbles=total_scrobbles,
                fetched_scrobbles=fetched_count,
                current_page=total_pages,
                total_pages=total_pages,
            )
            await progress_callback(progress)

        logger.info(f"Last.fm scrobble fetch complete: {fetched_count} total scrobbles")

    async def get_all_top_artists(
        self,
        username: str,
        period: str = "overall",
        max_artists: int = 1000,
        progress_callback: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all top artists for a user with pagination.

        Args:
            username: Last.fm username
            period: overall | 7day | 1month | 3month | 6month | 12month
            max_artists: Maximum number of artists to fetch (default 1000)
            progress_callback: Optional async callback for progress updates

        Returns:
            List of artist dicts with name, playcount, rank
        """
        artists: list[dict[str, Any]] = []
        page = 1
        per_page = 200  # Fetch in larger batches

        while len(artists) < max_artists:
            response = await self.get_top_artists(
                username=username,
                period=period,
                limit=per_page,
                page=page,
            )

            items = response.get("topartists", {}).get("artist", [])
            if not items:
                break

            # Get total from @attr for progress
            attr = response.get("topartists", {}).get("@attr", {})
            total = int(attr.get("total", 0))

            for item in items:
                if len(artists) >= max_artists:
                    break
                # Add rank based on position
                item["rank"] = len(artists) + 1
                artists.append(item)

            if progress_callback:
                progress = ScrobbleProgress(
                    total_scrobbles=min(total, max_artists),
                    fetched_scrobbles=len(artists),
                    current_page=page,
                    total_pages=(min(total, max_artists) + per_page - 1) // per_page,
                )
                await progress_callback(progress)

            logger.info(f"Last.fm top artists: fetched {len(artists)} artists")

            if len(items) < per_page:
                break
            page += 1

        return artists

    async def get_all_top_tracks(
        self,
        username: str,
        period: str = "overall",
        max_tracks: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch all top tracks for a user with pagination.

        Unlike get_all_scrobbles which returns individual scrobbles,
        this returns aggregated top tracks with play counts.

        Args:
            username: Last.fm username
            period: overall | 7day | 1month | 3month | 6month | 12month
            max_tracks: Maximum number of tracks to fetch

        Returns:
            List of track dicts with artist, name, playcount, rank
        """
        tracks: list[dict[str, Any]] = []
        page = 1
        per_page = 200

        while len(tracks) < max_tracks:
            response = await self.get_top_tracks(
                username=username,
                period=period,
                limit=per_page,
                page=page,
            )

            items = response.get("toptracks", {}).get("track", [])
            if not items:
                break

            for item in items:
                if len(tracks) >= max_tracks:
                    break
                # Add rank based on position
                item["rank"] = len(tracks) + 1
                tracks.append(item)

            logger.info(f"Last.fm top tracks: fetched {len(tracks)} tracks")

            if len(items) < per_page:
                break
            page += 1

        return tracks

    async def get_track_info(self, artist: str, track: str) -> dict[str, Any]:
        """Get information about a specific track."""
        return await self._api_request(
            "track.getInfo",
            {"artist": artist, "track": track},
        )

    def _generate_signature(self, params: dict[str, Any]) -> str:
        """Generate API signature for authenticated requests."""
        sorted_params = sorted(params.items())
        param_string = "".join(f"{k}{v}" for k, v in sorted_params)
        param_string += self.shared_secret
        return hashlib.md5(param_string.encode()).hexdigest()

    async def _api_request(
        self,
        method: str,
        params: dict[str, Any],
        authenticated: bool = False,
    ) -> dict[str, Any]:
        """Make an API request to Last.fm."""
        request_params = {
            "method": method,
            "api_key": self.api_key,
            "format": "json",
            **params,
        }

        if authenticated:
            request_params["api_sig"] = self._generate_signature(
                {k: v for k, v in request_params.items() if k != "format"}
            )

        async with httpx.AsyncClient() as client:
            response = await client.get(self.API_BASE, params=request_params)

            if response.status_code != 200:
                raise ExternalServiceError("Last.fm", f"API error: {response.text}")

            data: dict[str, Any] = response.json()

            # Last.fm returns errors in the response body
            if "error" in data:
                raise ExternalServiceError("Last.fm", f"{data.get('message', 'Unknown error')}")

            return data
