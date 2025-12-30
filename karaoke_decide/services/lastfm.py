"""Last.fm API client for Karaoke Decide."""

import hashlib
from typing import Any

import httpx

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError


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

    async def get_recent_tracks(self, username: str, limit: int = 50, page: int = 1) -> dict[str, Any]:
        """Get user's recent tracks with play timestamps."""
        return await self._api_request(
            "user.getrecenttracks",
            {"user": username, "limit": limit, "page": page, "extended": 1},
        )

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
