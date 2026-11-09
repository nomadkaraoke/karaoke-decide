"""Live Spotify track search for gen's song matching (client-credentials, app-level).

gen's match judge and free-text resolver use it to confirm that a typed song really exists
when our BigQuery catalogue (a July 2025 Spotify snapshot + MusicBrainz) doesn't know it, e.g.
a 2025 release. See ``docs/archive/2026-11-06-spotify-grounded-song-matching-plan.md`` in the
workspace.

Why it's careful:
- **Client-credentials token** on decide's Spotify app: it can't act as any user, so it never
  touches the Premium account flacfetch downloads with.
- **Shared quota.** Since July 2026, Development Mode quotas are counted per developer account,
  possibly shared with flacfetch's Spotify searches. So results are cached, and any 429 opens a
  circuit breaker: no Spotify call until the cooldown ends. Callers treat "unavailable" as "no
  evidence", never as an error.
- Feb 2026 API: ``limit`` is at most 10 and tracks no longer carry ``popularity``.
- **Kept forever** (2026-11-08): every fetch is stored by ``spotify_store.SpotifyStore``,
  including the raw items. A query seen before is answered from the store and never asks Spotify
  again. Lookup order: in-memory cache → permanent store → Spotify.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from backend.services.spotify_store import SearchStore

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
MAX_LIMIT = 10  # Spotify's cap since Feb 2026
REQUEST_TIMEOUT_S = 4.0
CACHE_TTL_S = 6 * 3600
CACHE_MAX = 2000
# A 429 without Retry-After (or a quota 429, whose Retry-After can be short while the bucket is
# still empty) still waits at least this long.
MIN_COOLDOWN_S = 600
TOKEN_REFRESH_MARGIN_S = 60


@dataclass(frozen=True)
class SpotifySearchResult:
    tracks: list[dict[str, Any]]
    available: bool  # False: not configured, breaker open, or Spotify failed — no evidence


def _norm_query(q: str) -> str:
    return " ".join((q or "").casefold().split())


def _track_view(t: dict[str, Any]) -> dict[str, Any] | None:
    name = (t.get("name") or "").strip()
    artists = [a.get("name", "").strip() for a in t.get("artists") or [] if a.get("name")]
    if not name or not artists:
        return None
    album = t.get("album") or {}
    release = album.get("release_date") or ""
    return {
        "track_id": t.get("id") or "",
        "track_name": name,
        "artist_name": artists[0],
        "artist_names": artists,
        "album_name": album.get("name") or "",
        "release_year": int(release[:4]) if release[:4].isdigit() else None,
        "duration_ms": t.get("duration_ms"),
        "isrc": (t.get("external_ids") or {}).get("isrc"),
    }


class SpotifySearchService:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.monotonic,
        store: SearchStore | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._token_expiry = 0.0
        self._cooldown_until = 0.0
        self._cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._token_lock = asyncio.Lock()
        self.store = store

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=self._transport)

    def _open_breaker(self, response: httpx.Response) -> None:
        try:
            retry_after = float(response.headers.get("Retry-After", "0"))
        except ValueError:
            retry_after = 0.0
        cooldown = max(retry_after, MIN_COOLDOWN_S)
        self._cooldown_until = self._clock() + cooldown
        reason = ""
        try:
            reason = (response.json().get("error") or {}).get("reason", "")
        except Exception:
            pass
        # WARNING, not ERROR: we degrade to "no evidence" and recover on our own. The cost is that
        # AI corrections aren't confident meanwhile; nobody needs paging for that.
        logger.warning("spotify-search: 429 %s, pausing Spotify calls for %ds", reason or "rate limited", cooldown)

    async def _get_token(self, client: httpx.AsyncClient) -> str | None:
        async with self._token_lock:
            if self._token and self._clock() < self._token_expiry - TOKEN_REFRESH_MARGIN_S:
                return self._token
            resp = await client.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
            )
            if resp.status_code == 429:
                self._open_breaker(resp)
                return None
            if resp.status_code != 200:
                logger.warning("spotify-search: token request failed (%s)", resp.status_code)
                return None
            body = resp.json()
            self._token = body.get("access_token")
            self._token_expiry = self._clock() + float(body.get("expires_in", 3600))
            return self._token

    def _cache_get(self, key: str) -> list[dict[str, Any]] | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        expiry, tracks = hit
        if self._clock() >= expiry:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return tracks

    def _cache_set(self, key: str, tracks: list[dict[str, Any]]) -> None:
        self._cache[key] = (self._clock() + CACHE_TTL_S, tracks)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)

    async def search_tracks(self, q: str, limit: int = MAX_LIMIT) -> SpotifySearchResult:
        query = _norm_query(q)
        limit = max(1, min(int(limit), MAX_LIMIT))
        if not query:
            return SpotifySearchResult([], available=False)
        # Always fetch Spotify's maximum and keep it all; callers get the first `limit`.
        cached = self._cache_get(query)
        if cached is None and self.store is not None:
            cached = await self.store.get(query)
            if cached is not None:
                self._cache_set(query, cached)
        if cached is not None:
            return SpotifySearchResult(cached[:limit], available=True)
        if not self.configured or self._clock() < self._cooldown_until:
            return SpotifySearchResult([], available=False)
        try:
            async with self._client() as client:
                token = await self._get_token(client)
                if not token:
                    return SpotifySearchResult([], available=False)
                resp = await client.get(
                    SEARCH_URL,
                    params={"q": query, "type": "track", "limit": MAX_LIMIT},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 401:
                    self._token = None  # expired early or revoked: next call fetches a new one
                    return SpotifySearchResult([], available=False)
                if resp.status_code == 429:
                    self._open_breaker(resp)
                    return SpotifySearchResult([], available=False)
                if resp.status_code != 200:
                    logger.warning("spotify-search: search failed (%s)", resp.status_code)
                    return SpotifySearchResult([], available=False)
                items = (resp.json().get("tracks") or {}).get("items") or []
        except httpx.HTTPError as exc:
            logger.warning("spotify-search: request error: %s", exc)
            return SpotifySearchResult([], available=False)
        raw = [t for t in items if isinstance(t, dict)]
        tracks = [v for v in (_track_view(t) for t in raw) if v]
        self._cache_set(query, tracks)
        if self.store is not None:
            await self.store.save(query, tracks, raw)  # never raises
        return SpotifySearchResult(tracks[:limit], available=True)


_service: SpotifySearchService | None = None


def get_spotify_search_service(
    client_id: str, client_secret: str, store: SearchStore | None = None
) -> SpotifySearchService:
    """Process-wide instance (one token, one cache, one breaker per Cloud Run instance).
    The first ``store`` passed is kept."""
    global _service
    if _service is None or (_service._client_id, _service._client_secret) != (client_id, client_secret):
        _service = SpotifySearchService(client_id, client_secret, store=store)
    elif _service.store is None and store is not None:
        _service.store = store
    return _service
