"""MetaBrainz OAuth client (PKCE S256) and ListenBrainz token/API-base changes."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from backend.config import BackendSettings
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.listenbrainz import ListenBrainzClient
from karaoke_decide.services.metabrainz_oauth import (
    MetaBrainzInvalidGrantError,
    MetaBrainzOAuth,
    new_pkce_pair,
)

MB = "https://mb.example"
LB = "https://lb.example/1"


@pytest.fixture
def settings() -> BackendSettings:
    return BackendSettings(
        metabrainz_base_url=MB + "/",
        metabrainz_client_id="cid",
        metabrainz_client_secret="csecret",
        public_api_base_url="https://decide.example",
        listenbrainz_api_base=LB,
    )


def test_pkce_pair_is_s256() -> None:
    verifier, challenge = new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_authorize_url(settings: BackendSettings) -> None:
    url = MetaBrainzOAuth(settings).authorize_url("st", "ch")
    assert url.startswith(f"{MB}/oauth2/authorize?")
    assert "scope=profile%20listenbrainz%3Asubmit-listens" in url  # %20, not '+'
    q = parse_qs(urlparse(url).query)
    assert q == {
        "response_type": ["code"],
        "client_id": ["cid"],
        "redirect_uri": ["https://decide.example/api/services/listenbrainz/oauth/callback"],
        "scope": ["profile listenbrainz:submit-listens"],
        "state": ["st"],
        "code_challenge": ["ch"],
        "code_challenge_method": ["S256"],
    }


@respx.mock
async def test_exchange_code_uses_basic_auth_and_verifier(settings: BackendSettings) -> None:
    route = respx.post(f"{MB}/oauth2/token").respond(
        200, json={"access_token": "meba_a", "refresh_token": "meba_r", "expires_in": 3600}
    )
    tokens = await MetaBrainzOAuth(settings).exchange_code("code1", "verifier1")
    assert tokens["access_token"] == "meba_a"
    req = route.calls.last.request
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()
    form = parse_qs(req.content.decode())
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["code1"],
        "redirect_uri": ["https://decide.example/api/services/listenbrainz/oauth/callback"],
        "code_verifier": ["verifier1"],
    }


@respx.mock
async def test_exchange_code_error_is_external_error(settings: BackendSettings) -> None:
    respx.post(f"{MB}/oauth2/token").respond(400, json={"error": "invalid_client"})
    with pytest.raises(ExternalServiceError) as exc:
        await MetaBrainzOAuth(settings).exchange_code("c", "v")
    assert not isinstance(exc.value, MetaBrainzInvalidGrantError)


@respx.mock
async def test_refresh(settings: BackendSettings) -> None:
    route = respx.post(f"{MB}/oauth2/token").respond(200, json={"access_token": "a2", "refresh_token": "r2"})
    assert (await MetaBrainzOAuth(settings).refresh("r1"))["refresh_token"] == "r2"
    assert parse_qs(route.calls.last.request.content.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["r1"],
    }


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"error": "invalid_grant"},
        {"error": "invalid_request", "error_description": "'refresh_token' in request was already revoked."},
    ],
)
async def test_refresh_dead_token_raises_invalid_grant(settings: BackendSettings, body: dict) -> None:
    respx.post(f"{MB}/oauth2/token").respond(400, json=body)
    with pytest.raises(MetaBrainzInvalidGrantError):
        await MetaBrainzOAuth(settings).refresh("r1")


@respx.mock
async def test_refresh_network_error(settings: BackendSettings) -> None:
    respx.post(f"{MB}/oauth2/token").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ExternalServiceError) as exc:
        await MetaBrainzOAuth(settings).refresh("r1")
    assert not isinstance(exc.value, MetaBrainzInvalidGrantError)


@respx.mock
async def test_userinfo(settings: BackendSettings) -> None:
    route = respx.get(f"{MB}/oauth2/userinfo").respond(200, json={"sub": "123", "username": "e2e-listener"})
    assert await MetaBrainzOAuth(settings).userinfo("meba_a") == "e2e-listener"
    assert route.calls.last.request.headers["authorization"] == "Bearer meba_a"


@respx.mock
@pytest.mark.parametrize("status,body", [(403, {"error": "expired access token"}), (200, {"sub": "1"})])
async def test_userinfo_errors(settings: BackendSettings, status: int, body: dict) -> None:
    respx.get(f"{MB}/oauth2/userinfo").respond(status, json=body)
    with pytest.raises(ExternalServiceError):
        await MetaBrainzOAuth(settings).userinfo("meba_a")


# --- ListenBrainzClient ------------------------------------------------------------------------


@respx.mock
async def test_lb_validate_token(settings: BackendSettings) -> None:
    route = respx.get(f"{LB}/validate-token").respond(
        200, json={"code": 200, "message": "Token valid.", "valid": True, "user_name": "e2e-listener"}
    )
    assert await ListenBrainzClient(settings).validate_token("tok") == "e2e-listener"
    assert route.calls.last.request.headers["authorization"] == "Token tok"


@respx.mock
async def test_lb_validate_token_invalid(settings: BackendSettings) -> None:
    respx.get(f"{LB}/validate-token").respond(200, json={"code": 200, "message": "Token invalid.", "valid": False})
    with pytest.raises(ExternalServiceError):
        await ListenBrainzClient(settings).validate_token("tok")


@respx.mock
async def test_lb_get_listens_uses_settings_base_and_1000_cap(settings: BackendSettings) -> None:
    route = respx.get(f"{LB}/user/fixture-user/listens").respond(200, json={"payload": {"listens": []}})
    await ListenBrainzClient(settings).get_listens("fixture-user", count=5000)
    assert route.calls.last.request.url.params["count"] == "1000"


def test_lb_default_base_without_settings() -> None:
    assert ListenBrainzClient().api_base == "https://api.listenbrainz.org/1"
