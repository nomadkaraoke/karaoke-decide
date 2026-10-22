"""MetaBrainz OAuth2 (authorization code + PKCE S256) for ListenBrainz.

Endpoints come from https://metabrainz.org/.well-known/openid-configuration:
``/oauth2/authorize``, ``/oauth2/token`` (client_secret_basic) and ``/oauth2/userinfo``.

ListenBrainz accepts the resulting ``meba_…`` access token on its authenticated API
(``Authorization: Token <token>``; LB reads the second word of the header and introspects
``meba_`` tokens against MetaBrainz), but ``/1/validate-token`` only knows LB user tokens,
so the username comes from MetaBrainz ``/oauth2/userinfo`` (LB usernames are MusicBrainz
usernames). Refresh rotates the refresh token: always store the one a refresh returns.
"""

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from backend.config import BackendSettings
from karaoke_decide.core.exceptions import ExternalServiceError

SCOPES = "profile listenbrainz:submit-listens"
CALLBACK_PATH = "/api/services/listenbrainz/oauth/callback"


class MetaBrainzInvalidGrantError(ExternalServiceError):
    """The refresh token is no longer usable (revoked, expired, or reused): the user must reconnect."""

    def __init__(self) -> None:
        super().__init__("MetaBrainz", "invalid_grant")


def new_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class MetaBrainzOAuth:
    """MetaBrainz OAuth client for the ListenBrainz connection."""

    TIMEOUT_SECONDS = 15.0

    def __init__(self, settings: BackendSettings):
        self.base_url = settings.metabrainz_base_url.rstrip("/")
        self.client_id = settings.metabrainz_client_id
        self.client_secret = settings.metabrainz_client_secret
        self.redirect_uri = settings.public_api_base_url.rstrip("/") + CALLBACK_PATH

    def authorize_url(self, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.base_url}/oauth2/authorize?{urlencode(params, quote_via=quote)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Raises MetaBrainzInvalidGrantError when the refresh token is dead."""
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def userinfo(self, access_token: str) -> str:
        """The MetaBrainz (= MusicBrainz = ListenBrainz) username the token belongs to."""
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    f"{self.base_url}/oauth2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as e:
            raise ExternalServiceError("MetaBrainz", f"userinfo failed: {type(e).__name__}")
        if resp.status_code != 200:
            raise ExternalServiceError("MetaBrainz", f"userinfo returned {resp.status_code}")
        username = _json(resp).get("username")
        if not isinstance(username, str) or not username:
            raise ExternalServiceError("MetaBrainz", "userinfo has no username")
        return username

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{self.base_url}/oauth2/token",
                    data=data,
                    auth=(self.client_id, self.client_secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as e:
            raise ExternalServiceError("MetaBrainz", f"token request failed: {type(e).__name__}")
        body = _json(resp)
        if resp.status_code != 200:
            error = body.get("error")
            description = str(body.get("error_description", "")).lower()
            # authlib answers a reused (rotated-away) refresh token with invalid_request "…revoked".
            if error == "invalid_grant" or (error == "invalid_request" and "revoked" in description):
                raise MetaBrainzInvalidGrantError()
            raise ExternalServiceError("MetaBrainz", f"token endpoint returned {resp.status_code} ({error})")
        if not body.get("access_token"):
            raise ExternalServiceError("MetaBrainz", "token response has no access_token")
        return body


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
