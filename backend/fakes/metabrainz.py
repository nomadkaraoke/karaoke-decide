"""Fake MetaBrainz OAuth2 (``/fake/metabrainz/oauth2``): authorize, token (PKCE S256), userinfo.

``authorize`` skips the consent page and redirects straight back with a code for the knob
``metabrainz_user`` (default ``fixture-user``), or with ``error=access_denied`` under knob
``metabrainz=deny``. Codes are single use; refresh tokens rotate, like MetaBrainz.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse

from backend.fakes.state import STATE, OAuthCode

router = APIRouter()

TOKEN_LIFETIME_S = 3600


def _redirect(redirect_uri: str, params: dict[str, str]) -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


def _error(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": error}, status_code=status)


@router.get("/oauth2/authorize", response_model=None)
async def authorize(
    redirect_uri: str,
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
) -> RedirectResponse | JSONResponse:
    if STATE.knob("metabrainz") == "deny":
        return _redirect(redirect_uri, {"error": "access_denied", "state": state})
    if code_challenge_method != "S256" or not code_challenge:
        return _error("invalid_request")
    code = f"fake-{STATE.next_id()}"
    STATE.codes[code] = OAuthCode(challenge=code_challenge, redirect_uri=redirect_uri, username=STATE.metabrainz_user)
    return _redirect(redirect_uri, {"code": code, "state": state})


def _issue(username: str) -> dict[str, Any]:
    n = STATE.next_id()
    access, refresh = f"meba_fake-access-{n}", f"fake-refresh-{n}"
    STATE.tokens[access] = username
    STATE.refresh_tokens[refresh] = username
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": TOKEN_LIFETIME_S,
        "scope": "profile listenbrainz:submit-listens",
    }


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@router.post("/oauth2/token")
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    grant = form.get("grant_type")
    if grant == "authorization_code":
        entry = STATE.codes.pop(str(form.get("code") or ""), None)
        verifier = str(form.get("code_verifier") or "")
        if entry is None or not verifier or _s256(verifier) != entry.challenge:
            return _error("invalid_grant")
        if form.get("redirect_uri") not in (None, entry.redirect_uri):
            return _error("invalid_grant")
        return JSONResponse(_issue(entry.username))
    if grant == "refresh_token":
        username = STATE.refresh_tokens.pop(str(form.get("refresh_token") or ""), None)
        if username is None:
            return _error("invalid_grant")
        return JSONResponse(_issue(username))
    return _error("unsupported_grant_type")


@router.get("/oauth2/userinfo")
async def userinfo(authorization: str | None = Header(None)) -> JSONResponse:
    scheme, _, access = (authorization or "").partition(" ")
    username = STATE.tokens.get(access.strip()) if scheme.lower() == "bearer" else None
    if username is None:
        return _error("invalid_token", 401)
    return JSONResponse({"sub": username, "username": username})
