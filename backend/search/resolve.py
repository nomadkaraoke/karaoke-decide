"""Gemini fallback proxy (spec §6): ``GET /api/search/resolve``.

Called by the app only when search found nothing (or for "Describe it"). Decide never
talks to Gemini itself: it posts the text to gen's ``/api/kjbox/catalog/resolve`` with a
server-side partner secret and maps the verdict to the kjbox ``ResolveResponse`` shape.
Every failure of the gen call (5xx, 429, timeout, bad body) is an expected transient
condition: it answers ``{}`` and logs at WARNING at most, never ERROR (ERROR pages #alerts).

Environment:
  GEN_API_URL                gen base URL (default https://api.nomadkaraoke.com)
  DECIDE_GEN_PARTNER_SECRET  partner secret; unset → resolve always answers {}
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

import httpx

from backend.search.models import ResolveResponse, SongRef, Split
from backend.search.ratelimit import RateLimits
from backend.search.service import SearchService
from karaoke_decide.song_search.identify import MAX_QUERY_CHARS
from karaoke_decide.song_search.text_normalize import song_key

logger = logging.getLogger(__name__)

DEFAULT_GEN_API_URL = "https://api.nomadkaraoke.com"
RESOLVE_PATH = "/api/kjbox/catalog/resolve"
# Above gen's worst case for a first-time description (~20 s: two 7 s Gemini attempts +
# a 4 s catalog tidy). At 12 s, a cold gen path (Gemini ~2 s, then a ~10 s catalog
# search) ran past this, so the first ask answered {} ("we couldn't work out…") while
# gen finished and cached the song for the second ask. The app has no fetch timeout
# and shows "Figuring it out…" meanwhile.
GEN_TIMEOUT_S = 25.0
CACHE_MAX = 500
MIN_QUERY_CHARS = 2
# R: tighter than search; each call may cost a Gemini request.
RESOLVE_LIMITS = RateLimits(per_device=20, per_ip=200, window_s=300)

_EMPTY = ResolveResponse()


class GenResolver:
    def __init__(
        self,
        service: SearchService | None,
        *,
        gen_url: str | None = None,
        secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service = service
        self._secret = (secret if secret is not None else os.environ.get("DECIDE_GEN_PARTNER_SECRET", "")).strip()
        base = (gen_url or os.environ.get("GEN_API_URL", "")).strip() or DEFAULT_GEN_API_URL
        self._url = base.rstrip("/") + RESOLVE_PATH
        self.timeout_s = GEN_TIMEOUT_S
        self._client = httpx.AsyncClient(timeout=GEN_TIMEOUT_S, transport=transport)
        self._cache: OrderedDict[str, ResolveResponse] = OrderedDict()
        self._lock = threading.Lock()
        if not self._secret:
            logger.warning("DECIDE_GEN_PARTNER_SECRET is not set: /api/search/resolve will answer {}")

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cached(self, key: str) -> ResolveResponse | None:
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
            return hit

    def _store(self, key: str, value: ResolveResponse) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)

    async def resolve(self, q: str) -> tuple[ResolveResponse, str]:
        """(response, outcome); outcome is the ``kind`` logged: a gen kind, ``cached``,
        ``skipped`` (no secret / query too short / no index) or ``error``."""
        q = " ".join((q or "").split())[:MAX_QUERY_CHARS]
        if len(q) < MIN_QUERY_CHARS or not self._secret or self._service is None:
            return _EMPTY, "skipped"
        key = q.casefold()
        hit = self._cached(key)
        if hit is not None:
            # The verdict applies, but echo THIS request's text as `typed`.
            return (hit.model_copy(update={"typed": q}) if hit.corrected is not None else hit), "cached"
        verdict = await self._call_gen(q)
        if verdict is None:
            return _EMPTY, "error"
        try:
            response = await asyncio.to_thread(self._map, q, verdict, self._service)
        except Exception as exc:  # noqa: BLE001 — an odd gen payload or index trouble must never 500
            logger.warning("resolve mapping failed: %s", type(exc).__name__)
            return _EMPTY, "error"
        if verdict.get("reason") != "unavailable":
            self._store(key, response)
        return response, str(verdict.get("kind"))

    async def _call_gen(self, q: str) -> dict[str, Any] | None:
        try:
            # httpx's timeout is per phase (a trickling body could outlast it); this bounds the whole call.
            r = await asyncio.wait_for(
                self._client.post(self._url, json={"query": q}, headers={"X-Partner-Secret": self._secret}),
                self.timeout_s,
            )
        except (TimeoutError, httpx.HTTPError) as exc:
            logger.warning("gen resolve unreachable after %gs: %s", self.timeout_s, type(exc).__name__)
            return None
        if r.status_code != 200:
            logger.warning("gen resolve answered HTTP %s", r.status_code)
            return None
        try:
            body = r.json()
        except ValueError:
            logger.warning("gen resolve returned a non-JSON body")
            return None
        if not isinstance(body, dict):
            logger.warning("gen resolve returned an unexpected body type")
            return None
        return body

    @staticmethod
    def _map(q: str, v: dict[str, Any], service: SearchService) -> ResolveResponse:
        """The kjbox ``search_resolve`` mapping (A54)."""
        kind = v.get("kind")
        s = lambda k: str(v.get(k) or "").strip()  # noqa: E731
        if kind in ("cosmetic", "content") and v.get("confident"):
            artist, title = s("canonical_artist"), s("canonical_title")
            if not (artist and title):
                return _EMPTY
            ref, groups = service.groups_for_song(artist, title)
            return ResolveResponse(corrected=ref, kind=kind, typed=q, groups=groups)
        if kind == "ambiguous":
            refs = []
            for a in v.get("alternatives") or []:
                if not (isinstance(a, dict) and isinstance(a.get("artist"), str) and isinstance(a.get("title"), str)):
                    continue
                key = song_key(a["artist"], a["title"])
                own = service._song_or_none(key)
                refs.append(
                    SongRef(
                        artist=a["artist"],
                        title=a["title"],
                        song_key=key,
                        popularity=own.get("pop") if own else None,
                        karaoke=own is not None and bool(own["versions"]),
                    )
                )
            return ResolveResponse(alternatives=refs) if refs else _EMPTY
        if kind == "none" and s("typed_artist") and s("typed_title") and s("reason") != "unavailable":
            return ResolveResponse(split=Split(artist=s("typed_artist"), title=s("typed_title")))
        return _EMPTY
