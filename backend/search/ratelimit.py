"""In-memory sliding-window rate limits, per device id and per client IP.

Per instance only (the search service runs at most a few instances), like kjbox's
limiter. Reusable: each endpoint family gets its own ``RateLimiter`` with its own
``RateLimits`` (resolve, Task A6, uses tighter numbers than search).
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

DEVICE_HEADER = "X-Device-Id"
CF_IP_HEADER = "CF-Connecting-IP"
# Set by the Cloudflare Worker (non-cf name: Cloudflare may overwrite CF-Connecting-IP on
# the Worker's subrequest to run.app).
CLIENT_IP_HEADER = "X-Nomad-Client-IP"
# Device ids are client-chosen; anything longer is truncated before keying.
MAX_DEVICE_ID_CHARS = 128


@dataclass(frozen=True)
class RateLimits:
    per_device: int
    per_ip: int
    window_s: float


# R2: the app debounces at 300 ms (kjbox 700 ms), so normal typing needs more than
# kjbox's 120/device; the per-IP cap still bounds abuse.
SEARCH_LIMITS = RateLimits(per_device=300, per_ip=1500, window_s=300)


class SlidingWindow:
    """At most ``limit`` hits per key in any ``window_s`` seconds. Thread-safe.
    Remembers at most ``max_keys`` keys (least recently used dropped first)."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 50_000,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._hits)

    def _live(self, key: str, now: float) -> deque[float]:
        q = self._hits.get(key)
        if q is None:
            q = self._hits[key] = deque()
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        cutoff = now - self.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def allowed(self, key: str) -> bool:
        """Would a hit now be within the limit? (Records nothing.)"""
        with self._lock:
            return len(self._live(key, self._clock())) < self.limit

    def hit(self, key: str) -> bool:
        """Record a hit if within the limit; False (nothing recorded) when over it."""
        with self._lock:
            now = self._clock()
            q = self._live(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Whole seconds until ``key`` may hit again (0 if it may now)."""
        with self._lock:
            now = self._clock()
            q = self._live(key, now)
            if len(q) < self.limit:
                return 0
            return max(1, math.ceil(q[0] + self.window_s - now))


class RateLimiter:
    """Per-device and per-IP windows. A request blocked by either spends neither budget."""

    def __init__(self, limits: RateLimits, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self._device = SlidingWindow(limits.per_device, limits.window_s, clock=clock)
        self._ip = SlidingWindow(limits.per_ip, limits.window_s, clock=clock)
        self._lock = threading.Lock()

    def check(self, *, device: str | None, ip: str | None) -> bool:
        with self._lock:
            if device and not self._device.allowed(device):
                return False
            if ip and not self._ip.allowed(ip):
                return False
            if device:
                self._device.hit(device)
            if ip:
                self._ip.hit(ip)
            return True

    def retry_after(self, *, device: str | None, ip: str | None) -> int:
        waits = [self._device.retry_after(device)] if device else []
        if ip:
            waits.append(self._ip.retry_after(ip))
        return max(waits, default=0) or 1


def device_id(request: Request) -> str | None:
    value = (request.headers.get(DEVICE_HEADER) or "").strip()
    return value[:MAX_DEVICE_ID_CHARS] or None


def client_ip(request: Request) -> str | None:
    """The Worker's X-Nomad-Client-IP, else CF-Connecting-IP, else the socket peer."""
    for header in (CLIENT_IP_HEADER, CF_IP_HEADER):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value
    return request.client.host if request.client else None


def rate_limit(limiter: RateLimiter) -> Callable[[Request], None]:
    """FastAPI dependency: 429 with ``Retry-After`` when over either limit."""

    def dependency(request: Request) -> None:
        device, ip = device_id(request), client_ip(request)
        if not limiter.check(device=device, ip=ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(limiter.retry_after(device=device, ip=ip))},
            )

    return dependency
