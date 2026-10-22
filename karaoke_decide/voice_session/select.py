"""Next-song choice: expected information gain on the voice type, with warm-up ordering.

Pure Python. The first song is the most comfortable of the user's most familiar songs; later songs
maximise the expected drop in voice-type entropy from the "where did it sit?" answer, weighted by how
well the user knows the song. A cold voice is only offered songs that stay near the comfortable range.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from karaoke_decide.voice_session.model import (
    HYPOTHESES,
    VOICE_TYPES,
    Hyp,
    Position,
    Posterior,
    SongDemand,
    comfort,
    likelihood_position,
    likely_octave,
)

Warmth = Literal["cold", "bit_warm", "warm"]
ReasonType = Literal["comfortable_start", "upper_range", "lower_range", "loved", "known", "explore"]

KNOW_SONG = 1.0
KNOW_ARTIST = 0.7
KNOW_UNKNOWN = 0.4
KNOW_SKIPPED_ARTIST = 0.2
SKIPS_TO_DOWNWEIGHT = 2

FIRST_POOL = 20
PREFILTER = 300
COLD_STRETCH_FIRST = 1.0  # attempts 0-1 while cold
COLD_STRETCH_LATER = 3.0
COLD_EARLY_ATTEMPTS = 2
PREF_WEIGHT = 0.03
STRATUM_PENALTY = 0.05
RECENT_STRATA = 2
REASON_MARGIN = 1.0

_POSITIONS: tuple[Position, ...] = ("too_low", "comfortable", "too_high", "both_ends")
_TYPES = list(VOICE_TYPES)
_TYPE_OF = [_TYPES.index(t) for t, _ in HYPOTHESES]


@dataclass(frozen=True)
class CandidateSong:
    song_key: str
    artist_key: str
    demand: SongDemand
    stratum: str
    know: float  # see know_weight()
    pref: float  # 3a artist weight, 0-1
    popularity: int  # 0-100
    loved: bool


@dataclass
class SessionState:
    """Selection/stopping state for one session.

    ``comfort_history`` holds ``posterior.comfort_estimate()`` before the first attempt and after each one.
    ``range_attempts`` counts attempts whose answers carry range evidence.
    """

    posterior: Posterior
    warmth: Warmth
    attempts: int
    range_attempts: int
    tired_count: int
    done_keys: set[str] = field(default_factory=set)
    recent_strata: list[str] = field(default_factory=list)
    comfort_history: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class NextPick:
    song_key: str
    reason_type: ReasonType
    score: float


def know_weight(
    song_key: str,
    artist_key: str,
    *,
    known_songs: set[str],
    loved_songs: set[str],
    artists: set[str],
    dont_know_skips: Mapping[str, int],
) -> float:
    """How likely the user knows the song well enough to sing it (CandidateSong.know).

    1.0 for a known or loved song, 0.7 for a song by an artist in the user's context, 0.4 otherwise,
    and 0.2 for an unfamiliar artist the user has already skipped twice as "don't know" this session.
    """
    if song_key in known_songs or song_key in loved_songs:
        return KNOW_SONG
    if artist_key in artists:
        return KNOW_ARTIST
    if dont_know_skips.get(artist_key, 0) >= SKIPS_TO_DOWNWEIGHT:
        return KNOW_SKIPPED_ARTIST
    return KNOW_UNKNOWN


@lru_cache(maxsize=8192)
def _answer_table(song: SongDemand) -> tuple[tuple[float, ...], ...]:
    """P(position answer | h) for each answer, over HYPOTHESES order (octave unknown, no pitch)."""
    return tuple(tuple(likelihood_position(h, song, a, None) for h in HYPOTHESES) for a in _POSITIONS)


def _stretch_at(h: Hyp, song: SongDemand) -> float:
    o = likely_octave(h, song)
    c_lo, c_hi = comfort(h)
    return max(song.p90 + o - c_hi, c_lo - (song.p10 + o))


@lru_cache(maxsize=8192)
def _stretch_vector(song: SongDemand) -> tuple[float, ...]:
    return tuple(_stretch_at(h, song) for h in HYPOTHESES)


def _probs(posterior: Posterior) -> list[float]:
    return [math.exp(posterior.log_p[h]) for h in HYPOTHESES]


def _entropy_of_masses(masses: list[float], total: float) -> float:
    """Entropy (nats) of masses normalised by total."""
    return -sum(m / total * math.log(m / total) for m in masses if m > 0)


def _type_entropy(p: list[float]) -> float:
    marg = [0.0] * len(_TYPES)
    for i, pi in enumerate(p):
        marg[_TYPE_OF[i]] += pi
    return _entropy_of_masses(marg, sum(marg))


def _eig(p: list[float], h0: float, song: SongDemand) -> float:
    expected = 0.0
    for row in _answer_table(song):
        joint = [0.0] * len(_TYPES)
        for i, (pi, li) in enumerate(zip(p, row, strict=True)):
            joint[_TYPE_OF[i]] += pi * li
        pa = sum(joint)
        if pa > 0:
            expected += pa * _entropy_of_masses(joint, pa)
    return max(0.0, h0 - expected)


def _expected_stretch(p: list[float], song: SongDemand) -> float:
    return sum(pi * s for pi, s in zip(p, _stretch_vector(song), strict=True))


def eig(posterior: Posterior, song: SongDemand) -> float:
    """Expected reduction (nats) in voice-type entropy from this song's position answer."""
    p = _probs(posterior)
    return _eig(p, _type_entropy(p), song)


def expected_stretch(posterior: Posterior, song: SongDemand) -> float:
    """Σ_h P(h)·max(d_high, d_low), each h at the octave it would most plausibly sing the song in."""
    return _expected_stretch(_probs(posterior), song)


def _familiarity(c: CandidateSong) -> float:
    return c.know * (0.5 + c.popularity / 200)


def _reason(posterior: Posterior, c: CandidateSong) -> ReasonType:
    lo, hi = posterior.comfort_estimate()
    top = max(posterior.log_p, key=lambda h: posterior.log_p[h])
    o = likely_octave(top, c.demand)  # labels describe the song in the octave the user would likely sing it
    if c.demand.p90 + o > hi + REASON_MARGIN:
        return "upper_range"
    if c.demand.p10 + o < lo - REASON_MARGIN:
        return "lower_range"
    if c.loved:
        return "loved"
    if c.know == KNOW_SONG:
        return "known"
    return "explore"


def choose_next(state: SessionState, cands: list[CandidateSong]) -> NextPick | None:
    """The next song to offer, or None when no candidate is eligible."""
    p = _probs(state.posterior)
    eligible = [c for c in cands if c.song_key not in state.done_keys]
    if state.warmth == "cold":
        limit = COLD_STRETCH_FIRST if state.attempts < COLD_EARLY_ATTEMPTS else COLD_STRETCH_LATER
        eligible = [c for c in eligible if _expected_stretch(p, c.demand) <= limit]
    if not eligible:
        return None
    familiar = sorted(eligible, key=lambda c: (-_familiarity(c), c.song_key))

    if state.attempts == 0:
        stretch = {c.song_key: _expected_stretch(p, c.demand) for c in familiar[:FIRST_POOL]}
        first = min(familiar[:FIRST_POOL], key=lambda c: (stretch[c.song_key], c.song_key))
        return NextPick(first.song_key, "comfortable_start", -stretch[first.song_key])

    h0 = _type_entropy(p)
    recent = set(state.recent_strata[-RECENT_STRATA:])

    def score(c: CandidateSong) -> float:
        penalty = STRATUM_PENALTY if c.stratum in recent else 0.0
        return _eig(p, h0, c.demand) * c.know + PREF_WEIGHT * c.pref - penalty

    scored = [(score(c), c) for c in familiar[:PREFILTER]]
    best_score, best = min(scored, key=lambda sc: (-sc[0], sc[1].song_key))
    return NextPick(best.song_key, _reason(state.posterior, best), best_score)
