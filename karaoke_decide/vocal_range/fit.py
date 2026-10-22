"""Song-fit scoring: can this user sing this song, and in which key / octave?

The algorithm is pinned and mirrored exactly by the mobile TS port; both are tested
against ``tests/fixtures/voice/fit_cases.json``. Do not change it without
regenerating the fixtures and updating the TS side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from karaoke_decide.vocal_range.notes import VOICE_TYPES

__all__ = [
    "KEY_PENALTY",
    "OCTAVE_PENALTY",
    "STRETCH_MAX",
    "TIER_TOLERANCE",
    "VOICE_TYPES",
    "FitBucket",
    "FitResult",
    "SongVocalRange",
    "UserVoiceRange",
    "VocalTier",
    "fit_song",
    "is_singable",
]

VocalTier = Literal["measured", "notes", "estimated", "rough"]
FitBucket = Literal["fits", "fitsIn", "stretch", "outside"]

OCTAVE_PENALTY = 0.75
KEY_PENALTY = 0.05
STRETCH_MAX = 2.0
TIER_TOLERANCE: dict[str, int] = {"measured": 0, "notes": 0, "estimated": 1, "rough": 2}

_OCTAVES = (0, -12, 12)  # ties prefer no octave shift, then down
_TRANSPOSES = (0, -1, 1, -2, 2, -3, 3, -4, 4)  # ties prefer smaller |t|, then down


@dataclass(frozen=True)
class SongVocalRange:
    low: float
    high: float
    p10: float
    p50: float
    p90: float
    tier: VocalTier
    octave_confidence: float | None = None


@dataclass(frozen=True)
class UserVoiceRange:
    low: int
    high: int
    comfort_low: int
    comfort_high: int


@dataclass(frozen=True)
class FitResult:
    bucket: FitBucket
    transpose: int
    octave: int
    cost: float
    score: float


def _outside(a: float, b: float, lo: float, hi: float) -> float:
    return max(0.0, lo - a) + max(0.0, b - hi)


def fit_song(user: UserVoiceRange, song: SongVocalRange) -> FitResult:
    tol = TIER_TOLERANCE[song.tier]
    confidence = 1.0 if song.octave_confidence is None else song.octave_confidence
    octave_free = song.tier == "notes" and confidence < 0.5

    best: tuple[float, float, int, int] | None = None  # (score, cost, t, o)
    for o in _OCTAVES:
        for t in _TRANSPOSES:
            s = t + o
            cost = _outside(
                song.p10 + s, song.p90 + s, user.comfort_low - tol, user.comfort_high + tol
            ) + 0.5 * _outside(song.low + s, song.high + s, user.low - tol, user.high + tol)
            score = cost + (0.0 if o == 0 or octave_free else OCTAVE_PENALTY) + KEY_PENALTY * abs(t)
            if best is None or score < best[0]:
                best = (score, cost, t, o)

    assert best is not None
    score, cost, t, o = best
    if cost == 0 and t == 0 and (o == 0 or octave_free):
        bucket: FitBucket = "fits"
    elif cost == 0:
        bucket = "fitsIn"
    elif cost <= STRETCH_MAX:
        bucket = "stretch"
    else:
        bucket = "outside"
    return FitResult(bucket, t, o, round(cost, 3), round(score, 3))


def is_singable(fit: FitResult) -> bool:
    """Anything but 'outside' (a stretch is singable with effort)."""
    return fit.bucket != "outside"
