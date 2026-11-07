"""Live Spotify search for gen's match judge: service (token, cache, breaker) + partner route."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api.deps import get_settings
from backend.config import BackendSettings
from backend.services import spotify_search as mod
from backend.services.spotify_search import MIN_COOLDOWN_S, SpotifySearchService


def _track(name: str, artist: str, *, release: str = "2025-03-07", isrc: str = "USQX92500001") -> dict[str, Any]:
    return {
        "id": f"id-{name}",
        "name": name,
        "artists": [{"name": artist}],
        "album": {"name": f"{name} (Single)", "release_date": release},
        "duration_ms": 209000,
        "external_ids": {"isrc": isrc},
    }


class FakeSpotify:
    """A MockTransport handler that records calls and answers token + search."""

    def __init__(self) -> None:
        self.token_calls = 0
        self.search_calls: list[dict[str, str]] = []
        self.search_status = 200
        self.search_headers: dict[str, str] = {}
        self.search_body: Any = {"tracks": {"items": [_track("Roll of Dice", "Cole Phillips")]}}
        self.token_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            self.token_calls += 1
            assert request.headers["Authorization"].startswith("Basic ")
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": f"tok{self.token_calls}", "expires_in": 3600})
        self.search_calls.append(dict(request.url.params))
        assert request.headers["Authorization"].startswith("Bearer tok")
        return httpx.Response(self.search_status, json=self.search_body, headers=self.search_headers)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def svc(fake: FakeSpotify, clock: Clock) -> SpotifySearchService:
    return SpotifySearchService("cid", "csecret", transport=httpx.MockTransport(fake), clock=clock)


async def test_search_returns_track_views(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    res = await svc.search_tracks("Roll of dice Cole Phillips")
    assert res.available is True
    assert res.tracks == [
        {
            "track_id": "id-Roll of Dice",
            "track_name": "Roll of Dice",
            "artist_name": "Cole Phillips",
            "artist_names": ["Cole Phillips"],
            "album_name": "Roll of Dice (Single)",
            "release_year": 2025,
            "duration_ms": 209000,
            "isrc": "USQX92500001",
        }
    ]
    assert fake.search_calls == [{"q": "roll of dice cole phillips", "type": "track", "limit": "10"}]


async def test_limit_is_capped_at_spotify_max(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    await svc.search_tracks("roll of dice", limit=50)
    assert fake.search_calls[0]["limit"] == "10"


async def test_token_reused_until_near_expiry(svc: SpotifySearchService, fake: FakeSpotify, clock: Clock) -> None:
    await svc.search_tracks("song one")
    await svc.search_tracks("song two")
    assert fake.token_calls == 1
    clock.t += 3600 - 30  # inside the refresh margin
    await svc.search_tracks("song three")
    assert fake.token_calls == 2


async def test_results_cached_per_normalised_query(svc: SpotifySearchService, fake: FakeSpotify, clock: Clock) -> None:
    await svc.search_tracks("Roll of  Dice")
    res = await svc.search_tracks("roll of dice")
    assert res.available and res.tracks
    assert len(fake.search_calls) == 1
    clock.t += mod.CACHE_TTL_S + 1
    await svc.search_tracks("roll of dice")
    assert len(fake.search_calls) == 2


async def test_429_opens_breaker_for_at_least_min_cooldown(
    svc: SpotifySearchService, fake: FakeSpotify, clock: Clock
) -> None:
    fake.search_status = 429
    fake.search_headers = {"Retry-After": "5"}
    fake.search_body = {"error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"}}
    res = await svc.search_tracks("first")
    assert res.available is False
    fake.search_status = 200
    clock.t += 60
    res = await svc.search_tracks("second")
    assert res.available is False
    assert len(fake.search_calls) == 1  # breaker open: Spotify not called again
    clock.t += MIN_COOLDOWN_S
    fake.search_body = {"tracks": {"items": [_track("X", "Y")]}}
    res = await svc.search_tracks("third")
    assert res.available is True and len(fake.search_calls) == 2


async def test_long_retry_after_is_honoured(svc: SpotifySearchService, fake: FakeSpotify, clock: Clock) -> None:
    fake.search_status = 429
    fake.search_headers = {"Retry-After": "7200"}
    await svc.search_tracks("first")
    clock.t += MIN_COOLDOWN_S + 1
    fake.search_status = 200
    assert (await svc.search_tracks("second")).available is False
    assert len(fake.search_calls) == 1


async def test_failures_are_unavailable_not_errors(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    fake.search_status = 500
    assert (await svc.search_tracks("boom")).available is False
    fake.search_status = 401
    assert (await svc.search_tracks("boom two")).available is False


async def test_401_drops_token(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    fake.search_status = 401
    await svc.search_tracks("a song")
    fake.search_status = 200
    await svc.search_tracks("another song")
    assert fake.token_calls == 2


async def test_token_failure_is_unavailable(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    fake.token_status = 400
    assert (await svc.search_tracks("song")).available is False
    assert fake.search_calls == []


async def test_network_error_is_unavailable(clock: Clock) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    svc = SpotifySearchService("cid", "cs", transport=httpx.MockTransport(boom), clock=clock)
    assert (await svc.search_tracks("song")).available is False


async def test_unconfigured_or_blank_query_never_calls_spotify(fake: FakeSpotify, clock: Clock) -> None:
    unconfigured = SpotifySearchService("", "", transport=httpx.MockTransport(fake), clock=clock)
    assert (await unconfigured.search_tracks("song")).available is False
    configured = SpotifySearchService("cid", "cs", transport=httpx.MockTransport(fake), clock=clock)
    assert (await configured.search_tracks("   ")).available is False
    assert fake.token_calls == 0


async def test_malformed_items_are_skipped(svc: SpotifySearchService, fake: FakeSpotify) -> None:
    fake.search_body = {
        "tracks": {"items": [{"name": "", "artists": []}, "junk", _track("Real", "Artist", release="")]}
    }
    res = await svc.search_tracks("real artist")
    assert [t["track_name"] for t in res.tracks] == ["Real"]
    assert res.tracks[0]["release_year"] is None


# --- route -------------------------------------------------------------------------------

SECRET = "gen-partner-secret"
URL = "/api/partner/gen/spotify-search"


def _client(monkeypatch: pytest.MonkeyPatch, secret: str) -> TestClient:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        decide_gen_partner_secret=secret,
        spotify_client_id="cid",
        spotify_client_secret="cs",
    )
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    c = TestClient(main.create_app())
    c.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    return c


@pytest.fixture
def route_fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSpotify]:
    fake = FakeSpotify()
    service = SpotifySearchService("cid", "cs", transport=httpx.MockTransport(fake))
    monkeypatch.setattr("backend.api.routes.partner_gen.get_spotify_search_service", lambda cid, cs: service)
    yield fake


def test_route_requires_partner_secret(monkeypatch: pytest.MonkeyPatch, route_fake: FakeSpotify) -> None:
    c = _client(monkeypatch, SECRET)
    assert c.get(URL, params={"q": "roll of dice"}).status_code == 401
    assert c.get(URL, params={"q": "roll of dice"}, headers={"X-Partner-Secret": "nope"}).status_code == 401
    assert route_fake.search_calls == []


def test_route_503_when_partner_unset(monkeypatch: pytest.MonkeyPatch, route_fake: FakeSpotify) -> None:
    c = _client(monkeypatch, "")
    assert c.get(URL, params={"q": "roll of dice"}, headers={"X-Partner-Secret": "x"}).status_code == 503


def test_route_returns_tracks(monkeypatch: pytest.MonkeyPatch, route_fake: FakeSpotify) -> None:
    c = _client(monkeypatch, SECRET)
    r = c.get(URL, params={"q": "Roll of dice Cole Phillips", "limit": 5}, headers={"X-Partner-Secret": SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["tracks"][0]["artist_name"] == "Cole Phillips"
    assert body["tracks"][0]["track_name"] == "Roll of Dice"
    assert route_fake.search_calls[0]["limit"] == "5"


def test_route_validates_query(monkeypatch: pytest.MonkeyPatch, route_fake: FakeSpotify) -> None:
    c = _client(monkeypatch, SECRET)
    h = {"X-Partner-Secret": SECRET}
    assert c.get(URL, params={"q": "a"}, headers=h).status_code == 422
    assert c.get(URL, params={"q": "ab", "limit": 11}, headers=h).status_code == 422


def test_get_spotify_search_service_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_service", None)
    a = mod.get_spotify_search_service("cid", "cs")
    assert mod.get_spotify_search_service("cid", "cs") is a
    assert mod.get_spotify_search_service("other", "cs") is not a
