"""flacfetch client — sourceability pre-check.

``POST /search`` on the flacfetch service is a pure dry-run: it queries the
torrent trackers (RED/OPS) and returns candidate results with quality/seeder
metadata, but downloads nothing and creates no job/keeper (the ``search_id`` it
returns self-expires — we ignore it).

We use it to answer one question: *can this track be sourced as a high-quality
FLAC?* A high-quality FLAC hit = a RED/OPS result that is lossless, has enough
seeders, and matches the requested track closely (``match_score``), so we don't
green-light a song only to grab the wrong album track later.

flacfetch internally paces ~1.1s between tracker calls; the generator adds
additional spacing between our ``/search`` calls to respect tracker rate limits.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from karaoke_decide.core.exceptions import ExternalServiceError

_HQ_PROVIDERS = {"RED", "OPS"}


@dataclass(frozen=True)
class FlacHit:
    """A qualifying high-quality FLAC result."""

    provider: str
    format: str
    bit_depth: int | None
    sample_rate: int | None
    seeders: int
    match_score: float
    release_type: str
    year: int | None
    size_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "format": self.format,
            "bit_depth": self.bit_depth,
            "sample_rate": self.sample_rate,
            "seeders": self.seeders,
            "match_score": self.match_score,
            "release_type": self.release_type,
            "year": self.year,
            "size_bytes": self.size_bytes,
        }


class FlacfetchClient:
    """Async client for the flacfetch search API."""

    def __init__(
        self, base_url: str, api_key: str, timeout: float = 150.0, max_retries: int = 2
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    async def search(
        self, artist: str, title: str, exhaustive: bool = False
    ) -> list[dict[str, Any]]:
        """Raw flacfetch search results (may be empty). Never downloads.

        Retries transient 429/5xx errors with backoff; raises
        ExternalServiceError only if every attempt fails.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        body = {"artist": artist, "title": title, "exhaustive": exhaustive}
        last_error = "unknown"
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/search", json=body, headers=headers
                    )
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if resp.status_code == 404:
                    return []  # documented "no results"
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    return results if isinstance(results, list) else []
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise ExternalServiceError("flacfetch", f"HTTP {resp.status_code}")
                last_error = f"HTTP {resp.status_code}"
            if attempt < self.max_retries - 1:
                await asyncio.sleep(3.0 * (attempt + 1))
        raise ExternalServiceError("flacfetch", last_error)

    @staticmethod
    def best_flac(
        results: list[dict[str, Any]],
        min_seeders: int = 1,
        min_match_score: float = 0.8,
    ) -> FlacHit | None:
        """Pick the best qualifying high-quality FLAC, or None.

        Qualifying = RED/OPS + lossless + seeders >= min + match_score >= min.
        Ranked by (match_score, seeders) descending.
        """
        best: FlacHit | None = None
        best_rank: tuple[float, int] = (-1.0, -1)
        for item in results:
            if item.get("provider") not in _HQ_PROVIDERS:
                continue
            if not item.get("is_lossless"):
                continue
            seeders = int(item.get("seeders") or 0)
            match_score = float(item.get("match_score") or 0.0)
            if seeders < min_seeders or match_score < min_match_score:
                continue
            quality = item.get("quality_data") or {}
            rank = (match_score, seeders)
            if rank > best_rank:
                best_rank = rank
                best = FlacHit(
                    provider=item.get("provider", ""),
                    format=quality.get("format", "FLAC"),
                    bit_depth=quality.get("bit_depth"),
                    sample_rate=quality.get("sample_rate"),
                    seeders=seeders,
                    match_score=match_score,
                    release_type=item.get("release_type", ""),
                    year=item.get("year"),
                    size_bytes=item.get("target_file_size") or item.get("size_bytes"),
                )
        return best
