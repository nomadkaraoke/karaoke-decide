"""Spotify API client for Karaoke Decide."""

from typing import Any

import httpx

from karaoke_decide.core.config import Settings
from karaoke_decide.core.exceptions import ExternalServiceError, RateLimitError


class SpotifyClient:
    """Client for Spotify Web API."""

    AUTH_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1"

    SCOPES = [
        "user-read-private",
        "user-read-email",
        "user-library-read",
        "user-top-read",
        "user-read-recently-played",
        "playlist-read-private",
    ]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client_id = settings.spotify_client_id
        self.client_secret = settings.spotify_client_secret
        self.redirect_uri = settings.spotify_redirect_uri

    def get_auth_url(self, state: str) -> str:
        """Get OAuth authorization URL."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.SCOPES),
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                auth=(self.client_id, self.client_secret),
            )

            if response.status_code != 200:
                raise ExternalServiceError("Spotify", f"Token exchange failed: {response.text}")

            result: dict[str, Any] = response.json()
            return result

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                auth=(self.client_id, self.client_secret),
            )

            if response.status_code != 200:
                raise ExternalServiceError("Spotify", f"Token refresh failed: {response.text}")

            result: dict[str, Any] = response.json()
            return result

    async def get_current_user(self, access_token: str) -> dict[str, Any]:
        """Get current user's profile."""
        return await self._api_request("GET", "/me", access_token)

    async def get_saved_tracks(self, access_token: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Get user's saved tracks."""
        return await self._api_request(
            "GET",
            "/me/tracks",
            access_token,
            params={"limit": limit, "offset": offset},
        )

    async def get_top_tracks(
        self,
        access_token: str,
        time_range: str = "medium_term",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get user's top tracks."""
        return await self._api_request(
            "GET",
            "/me/top/tracks",
            access_token,
            params={"time_range": time_range, "limit": limit, "offset": offset},
        )

    async def get_recently_played(self, access_token: str, limit: int = 50) -> dict[str, Any]:
        """Get user's recently played tracks."""
        return await self._api_request(
            "GET",
            "/me/player/recently-played",
            access_token,
            params={"limit": limit},
        )

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        access_token: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated API request."""
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise RateLimitError("Spotify", f"Rate limited. Retry after {retry_after}s")

            if response.status_code != 200:
                raise ExternalServiceError("Spotify", f"API error: {response.text}")

            result: dict[str, Any] = response.json()
            return result
