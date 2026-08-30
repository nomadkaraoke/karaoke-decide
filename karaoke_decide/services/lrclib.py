"""LRCLIB client — fetch lyrics text for a track.

LRCLIB (https://lrclib.net) is a free, no-key lyrics database. We use it as the
*source of lyrics text* for our richness heuristics — not as a synced-vs-plain
gate (karaoke-gen does its own AudioShake transcription + alignment, so whether
LRCLIB has *synced* lyrics is irrelevant). A track with no LRCLIB lyrics at all
is dropped, because we have no cheap way to assess it.

Be polite: send a descriptive User-Agent and throttle. The public instance is
generous but we cache lyrics forever, so we only hit it once per song ever.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from karaoke_decide.candidates.matching import (
    artist_variants,
    norm,
    strip_decorations,
    title_variants,
)
from karaoke_decide.core.exceptions import ExternalServiceError


class LrclibClient:
    """Async client for the LRCLIB search API."""

    API_BASE = "https://lrclib.net/api"

    def __init__(self, user_agent: str, timeout: float = 20.0, max_retries: int = 3):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries

    async def search(self, artist: str, title: str) -> list[dict[str, Any]]:
        """Raw LRCLIB search results for an artist/title (may be empty).

        Retries transient 429/5xx errors with backoff; raises
        ExternalServiceError only if every attempt fails.
        """
        params = {"artist_name": artist, "track_name": strip_decorations(title)}
        headers = {"User-Agent": self.user_agent}
        last_error = "unknown"
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(f"{self.API_BASE}/search", params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if resp.status_code == 404:
                    return []
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else []
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise ExternalServiceError("LRCLIB", f"HTTP {resp.status_code}")
                last_error = f"HTTP {resp.status_code}"
            if attempt < self.max_retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise ExternalServiceError("LRCLIB", last_error)

    async def best_lyrics(self, artist: str, title: str) -> dict[str, Any] | None:
        """Return the best matching lyrics record for the track, or None.

        A "match" means the result's artist AND title normalize to one of the
        query's variants. Among matches we prefer the record with the most
        plain-lyrics content (richest transcription of the same song).
        Returns a dict: {plain, synced, instrumental, matched_artist,
        matched_title, duration}.
        """
        results = await self.search(artist, title)
        want_artists = artist_variants(artist)
        want_titles = title_variants(title)

        best: dict[str, Any] | None = None
        best_len = -1
        for item in results:
            r_artist = item.get("artistName") or ""
            r_title = item.get("trackName") or ""
            if norm(r_artist) not in want_artists:
                # allow the query artist to be a variant of the result too
                if not (artist_variants(r_artist) & want_artists):
                    continue
            if norm(r_title) not in want_titles:
                if not (title_variants(r_title) & want_titles):
                    continue
            plain = item.get("plainLyrics") or ""
            synced = item.get("syncedLyrics") or ""
            # Prefer the richest available text for scoring.
            text_len = len(plain) or len(synced)
            if text_len > best_len:
                best_len = text_len
                best = {
                    "plain": plain,
                    "synced": synced,
                    "instrumental": bool(item.get("instrumental")),
                    "matched_artist": r_artist,
                    "matched_title": r_title,
                    "duration": item.get("duration"),
                }
        return best
