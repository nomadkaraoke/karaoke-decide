"""Stratified pool sampler: greedy marginal balancing across voice dimensions."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass

from karaoke_decide.catalog.moods import energy_band
from karaoke_decide.voice_pool.models import PoolCandidate

MIN_POOL = 600
_CENTRES = {"male": 57.0, "female": 64.0}
_DEFAULT_CENTRE = 60.0
_KNOWN_DECADES = ("pre1980", "1980s", "1990s", "2000s", "2010s+")


def _gender(c: PoolCandidate) -> str:
    g = (c.lead_gender or "").lower()
    return g if g in ("male", "female", "mixed") else "unknown"


def _register(c: PoolCandidate) -> str:
    if c.vr_p50 is None:
        return "unknown"
    rel = c.vr_p50 - _CENTRES.get(_gender(c), _DEFAULT_CENTRE)
    if rel < -3:
        return "low"
    if rel > 3:
        return "high"
    return "mid"


def _span(c: PoolCandidate) -> str:
    if c.vr_span is None:
        return "unknown"
    if c.vr_span < 10:
        return "narrow"
    if c.vr_span > 15:
        return "wide"
    return "mid"


def _decade(c: PoolCandidate) -> str:
    return c.decade if c.decade in _KNOWN_DECADES else "unknown"


def _energy(c: PoolCandidate) -> str:
    return energy_band(c.energy) or "unknown"


def _duet(c: PoolCandidate) -> str:
    return "duet" if c.duet else "solo"


DIMENSIONS: dict[str, Callable[[PoolCandidate], str]] = {
    "gender": _gender,
    "register": _register,
    "span": _span,
    "decade": _decade,
    "energy": _energy,
    "duet": _duet,
}

TARGET_SHARES: dict[str, dict[str, float]] = {
    "gender": {"male": 0.45, "female": 0.40, "mixed": 0.10, "unknown": 0.05},
    "register": {"low": 0.3, "mid": 0.3, "high": 0.3, "unknown": 0.1},
    "span": {"narrow": 0.3, "mid": 0.3, "wide": 0.3, "unknown": 0.1},
    "decade": {
        "pre1980": 0.18,
        "1980s": 0.18,
        "1990s": 0.18,
        "2000s": 0.18,
        "2010s+": 0.18,
        "unknown": 0.10,
    },
    "energy": {"low": 0.3, "mid": 0.35, "high": 0.3, "unknown": 0.05},
    "duet": {"solo": 0.9, "duet": 0.1},
}


def stratum_label(c: PoolCandidate) -> str:
    return "|".join(f"{d}={fn(c)}" for d, fn in DIMENSIONS.items())


def _priority(c: PoolCandidate) -> float:
    return c.popularity / 100 + 0.3 * c.has_ultrastar + 0.2 * (c.vr_tier == "measured")


def select_pool(
    cands: list[PoolCandidate],
    n: int,
    *,
    max_per_artist: int = 2,
    pre_cap: int = 8000,
) -> list[tuple[PoolCandidate, str]]:
    """Pick up to ``n`` candidates balanced across ``DIMENSIONS``.

    Keeps the ``pre_cap`` highest-priority candidates (ties: song_key asc),
    then greedily picks the candidate with the best
    ``sum_d unmet_share(d, value) + 0.5 * priority``, where unmet_share is
    ``max(0, target - count) / target`` with an unrounded ``target = share * n``.
    Scores only ever decrease as counts grow, so a lazy max-heap gives the exact
    greedy result without rescoring every candidate each round. Ties break on
    song_key ascending. Returns ``(candidate, stratum_label)`` in pick order.
    """
    if n <= 0 or not cands:
        return []
    prios = {c.song_key: _priority(c) for c in cands}
    pool = sorted(cands, key=lambda c: (-prios[c.song_key], c.song_key))[:pre_cap]

    target = {d: {v: s * n for v, s in shares.items()} for d, shares in TARGET_SHARES.items()}
    count = {d: {v: 0 for v in shares} for d, shares in TARGET_SHARES.items()}
    values = {c.song_key: tuple((d, fn(c)) for d, fn in DIMENSIONS.items()) for c in pool}

    def score(c: PoolCandidate) -> float:
        total = 0.0
        for d, v in values[c.song_key]:
            t = target[d][v]
            total += max(0.0, t - count[d][v]) / t
        return total + 0.5 * prios[c.song_key]

    heap = [(-score(c), c.song_key, i) for i, c in enumerate(pool)]
    heapq.heapify(heap)
    artist_counts: dict[str, int] = {}
    picked: list[tuple[PoolCandidate, str]] = []
    while heap and len(picked) < n:
        neg, key, i = heapq.heappop(heap)
        c = pool[i]
        if artist_counts.get(c.artist_key, 0) >= max_per_artist:
            continue  # artist is full for good
        fresh = -score(c)
        if fresh != neg:
            heapq.heappush(heap, (fresh, key, i))
            continue
        picked.append((c, stratum_label(c)))
        artist_counts[c.artist_key] = artist_counts.get(c.artist_key, 0) + 1
        for d, v in values[key]:
            count[d][v] += 1
    return picked


@dataclass
class CoverageReport:
    target: int
    picked: int
    per_dimension: dict[str, dict[str, tuple[int, int]]]  # dim -> value -> (target_count, actual)
    empty_values: list[str]  # "dim=value" with target > 0 and actual 0
    below_minimum: bool


def coverage(picked: list[tuple[PoolCandidate, str]], n: int) -> CoverageReport:
    """Compare a pick against the targets; ``target_count = round(share * n)``."""
    per: dict[str, dict[str, tuple[int, int]]] = {}
    empty: list[str] = []
    for d, fn in DIMENSIONS.items():
        actual = {v: 0 for v in TARGET_SHARES[d]}
        for c, _ in picked:
            actual[fn(c)] += 1
        per[d] = {v: (round(TARGET_SHARES[d][v] * n), actual[v]) for v in TARGET_SHARES[d]}
        empty += [f"{d}={v}" for v, (t, a) in per[d].items() if t > 0 and a == 0]
    return CoverageReport(n, len(picked), per, empty, len(picked) < MIN_POOL)
