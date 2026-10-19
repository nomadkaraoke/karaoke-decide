"""Per-device + per-IP sliding-window rate limits (R2: /api/search 300/device, 1500/IP per 5 min)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.search.ratelimit import SEARCH_LIMITS, RateLimiter, RateLimits, SlidingWindow


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_search_limits_are_the_r2_numbers() -> None:
    assert SEARCH_LIMITS == RateLimits(per_device=300, per_ip=1500, window_s=300)


def test_sliding_window_allows_limit_then_blocks_until_expiry() -> None:
    clock = FakeClock()
    w = SlidingWindow(limit=3, window_s=300, clock=clock)
    assert [w.hit("k") for _ in range(4)] == [True, True, True, False]
    clock.t += 299
    assert w.hit("k") is False
    clock.t += 2  # the first three hits are now older than the window
    assert w.hit("k") is True
    assert w.hit("other") is True


def test_sliding_window_retry_after() -> None:
    clock = FakeClock()
    w = SlidingWindow(limit=1, window_s=300, clock=clock)
    w.hit("k")
    clock.t += 100
    assert w.retry_after("k") == 200


def test_sliding_window_bounds_key_count() -> None:
    clock = FakeClock()
    w = SlidingWindow(limit=5, window_s=300, clock=clock, max_keys=10)
    for i in range(50):
        w.hit(f"k{i}")
    assert len(w) <= 10


def test_device_limit_300_then_429() -> None:
    limiter = RateLimiter(SEARCH_LIMITS, clock=FakeClock())
    assert all(limiter.check(device="d1", ip="1.1.1.1") for _ in range(300))
    assert limiter.check(device="d1", ip="1.1.1.1") is False
    # Another device behind the same IP is still fine.
    assert limiter.check(device="d2", ip="1.1.1.1") is True


def test_ip_limit_1500_across_devices() -> None:
    limiter = RateLimiter(SEARCH_LIMITS, clock=FakeClock())
    for i in range(1500):
        assert limiter.check(device=f"d{i}", ip="9.9.9.9")
    assert limiter.check(device="fresh", ip="9.9.9.9") is False
    assert limiter.check(device="fresh", ip="8.8.8.8") is True


def test_no_device_header_is_limited_by_ip_only() -> None:
    limiter = RateLimiter(RateLimits(per_device=2, per_ip=5, window_s=300), clock=FakeClock())
    assert all(limiter.check(device=None, ip="1.2.3.4") for _ in range(5))
    assert limiter.check(device=None, ip="1.2.3.4") is False


def test_blocked_device_request_does_not_spend_ip_budget() -> None:
    limiter = RateLimiter(RateLimits(per_device=1, per_ip=3, window_s=300), clock=FakeClock())
    assert limiter.check(device="d", ip="ip")
    assert not limiter.check(device="d", ip="ip")
    assert not limiter.check(device="d", ip="ip")
    assert limiter.check(device="e", ip="ip")
    assert limiter.check(device="f", ip="ip")
    assert not limiter.check(device="g", ip="ip")


# --- over HTTP ---------------------------------------------------------------


@pytest.fixture
def small_client(index_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from backend.search.app import create_search_app

    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    app = create_search_app(search_limits=RateLimits(per_device=3, per_ip=5, window_s=300))
    with TestClient(app) as c:
        yield c


def test_api_device_limit_returns_429_with_retry_after(small_client: TestClient) -> None:
    h = {"X-Device-Id": "phone"}
    for _ in range(3):
        assert small_client.get("/api/search/popular", headers=h).status_code == 200
    r = small_client.get("/api/search", params={"q": "baby"}, headers=h)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_api_ip_from_cf_connecting_ip(small_client: TestClient) -> None:
    for i in range(5):
        h = {"X-Device-Id": f"d{i}", "CF-Connecting-IP": "203.0.113.7"}
        assert small_client.get("/api/search/popular", headers=h).status_code == 200
    blocked = {"X-Device-Id": "d99", "CF-Connecting-IP": "203.0.113.7"}
    assert small_client.get("/api/search/popular", headers=blocked).status_code == 429
    # Another CF client IP (same TestClient host) has its own budget.
    other = {"X-Device-Id": "d100", "CF-Connecting-IP": "203.0.113.8"}
    assert small_client.get("/api/search/popular", headers=other).status_code == 200


def test_api_ip_falls_back_to_client_host(small_client: TestClient) -> None:
    for i in range(5):
        assert small_client.get("/api/search/popular", headers={"X-Device-Id": f"x{i}"}).status_code == 200
    assert small_client.get("/api/search/popular", headers={"X-Device-Id": "x9"}).status_code == 429


def test_api_event_is_limited_too(small_client: TestClient) -> None:
    h = {"X-Device-Id": "ev"}
    body = {"sid": "s", "type": "not_it", "data": {}}
    for _ in range(3):
        assert small_client.post("/api/search/event", json=body, headers=h).status_code == 204
    assert small_client.post("/api/search/event", json=body, headers=h).status_code == 429


def test_api_default_device_limit_is_300(client: TestClient) -> None:
    h = {"X-Device-Id": "busy-phone"}
    for _ in range(300):
        assert client.get("/api/search/popular", params={"limit": 1}, headers=h).status_code == 200
    assert client.get("/api/search/popular", params={"limit": 1}, headers=h).status_code == 429


def _req(headers: dict[str, str], peer: str | None = "10.0.0.1"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


def test_client_ip_prefers_worker_header_then_cf_then_peer() -> None:
    from backend.search.ratelimit import client_ip

    both = _req({"X-Nomad-Client-IP": "1.1.1.1", "CF-Connecting-IP": "2.2.2.2"})
    assert client_ip(both) == "1.1.1.1"
    assert client_ip(_req({"CF-Connecting-IP": "2.2.2.2"})) == "2.2.2.2"
    assert client_ip(_req({})) == "10.0.0.1"
    assert client_ip(_req({}, peer=None)) is None
