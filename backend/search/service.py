"""SearchService: song identification + karaoke availability for one query.

Songs are joined across song_id.db and availability.db by ``song_key`` =
``text_normalize.song_key(display artist, display title)`` of the song_id row (R9).
"""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from backend.search.models import Identification, SongDetail, SongGroup, SongRef, Version
from karaoke_decide.song_search.availability import AvailabilityIndex
from karaoke_decide.song_search.identify import MAX_QUERY_CHARS, SongIdentifier
from karaoke_decide.song_search.identify_kind import identify_kind
from karaoke_decide.song_search.text_normalize import song_key

logger = logging.getLogger(__name__)

# Shortest query (after strip) searched at all; shorter → the app shows popular songs (R8).
MIN_QUERY_CHARS = 2
# Versions per group in list responses; the song page returns them all.
LIST_VERSION_CAP = 12
# Groups per search response.
MAX_GROUPS = 60
CANDIDATE_LIMIT = 5


@dataclass(frozen=True)
class SearchResult:
    identification: Identification
    groups: list[SongGroup]


def _clamp(limit: int, hi: int) -> int:
    return max(1, min(int(limit), hi))


def _ref(match: dict[str, Any]) -> SongRef:
    return SongRef(
        artist=match["artist"],
        title=match["title"],
        song_key=song_key(match["artist"], match["title"]),
        popularity=match.get("popularity"),
        karaoke=bool(match.get("karaoke")),
    )


def _group(g: dict[str, Any], cap: int | None = LIST_VERSION_CAP) -> SongGroup:
    versions = g["versions"] if cap is None else g["versions"][:cap]
    return SongGroup(
        song_key=g["song_key"],
        artist=g["artist"],
        title=g["title"],
        version_count=g["version_count"],
        versions=[Version(**v) for v in versions],
    )


_NONE = Identification(status="none")


class SearchService:
    def __init__(
        self,
        identifier: SongIdentifier,
        availability: AvailabilityIndex,
        *,
        executor: Executor | None = None,
    ) -> None:
        self.identifier = identifier
        self.availability = availability
        self._executor = executor or ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")

    def _identify(self, q: str) -> dict[str, Any]:
        try:
            return self.identifier.identify(q, limit=CANDIDATE_LIMIT)
        except Exception:
            # One bad query must not fail the search; availability still answers.
            logger.warning("identify failed for %r", q, exc_info=True)
            return {"status": "none", "best": None, "candidates": []}

    def search(self, q: str, limit: int = 20) -> SearchResult:
        q = (q or "").strip()[:MAX_QUERY_CHARS]
        if len(q) < MIN_QUERY_CHARS:
            return SearchResult(_NONE, [])
        limit = _clamp(limit, MAX_GROUPS)

        ident_f = self._executor.submit(self._identify, q)
        typed_f = self._executor.submit(self.availability.search, q, limit)
        raw = ident_f.result()
        status = raw.get("status", "none")
        best = _ref(raw["best"]) if raw.get("best") and status != "none" else None

        merged: dict[str, dict[str, Any]] = {}
        if status == "confident" and best is not None:
            # kjbox fetchKaraokeForCorrection: also look up what the singer meant.
            corrected = self.availability.search(f"{best.artist} {best.title}", limit)
            own = next((g for g in corrected if g["song_key"] == best.song_key), None)
            if own is None:
                own = self._song_or_none(best.song_key)
            if own is not None:
                merged[own["song_key"]] = own
            for g in corrected:
                merged.setdefault(g["song_key"], g)
        for g in typed_f.result():
            merged.setdefault(g["song_key"], g)

        identification = Identification(
            status=status,
            kind=identify_kind(q, best.artist, best.title) if status == "confident" and best else None,
            best=best,
            candidates=[_ref(c) for c in raw.get("candidates", [])] if status != "none" else [],
        )
        groups = [_group(g) for g in list(merged.values())[:limit]]
        return SearchResult(identification, groups)

    def _song_or_none(self, key: str) -> dict[str, Any] | None:
        """``availability.song`` for the search path: index trouble degrades to no
        group, like ``availability.search`` does, instead of failing the search."""
        try:
            return self.availability.song(key)
        except sqlite3.Error:
            logger.warning("availability song lookup failed for %r", key, exc_info=True)
            return None

    def popular(self, limit: int = 30) -> list[SongGroup]:
        return [_group(g) for g in self.availability.popular(_clamp(limit, MAX_GROUPS))]

    def song(self, key: str) -> SongDetail | None:
        g = self.availability.song(key)
        if g is None:
            return None
        ref = SongRef(
            artist=g["artist"],
            title=g["title"],
            song_key=g["song_key"],
            popularity=g["pop"],
            karaoke=bool(g["versions"]),
        )
        return SongDetail(song=ref, versions=[Version(**v) for v in g["versions"]])

    def close(self) -> None:
        """Stop the pool, then close the index connections (shutdown only)."""
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.identifier.close()
        self.availability.close()
