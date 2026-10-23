"""Simulated singers for checking that selection + model + stopping converge on the true voice type.

A simulated singer with true hypothesis h sings each offered song in the octave h would most plausibly
use (likely_octave) and answers the position and difficulty questions by sampling the model's own
likelihoods at that octave, with `noise` as the chance of a uniformly random answer. No pitch.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from karaoke_decide.voice_session.model import (
    HYPOTHESES,
    VOICE_TYPES,
    Context,
    Difficulty,
    Evidence,
    Hyp,
    Position,
    Posterior,
    Quiz,
    SongDemand,
    _register_band,
    likelihood_difficulty,
    likelihood_position,
    likely_octave,
)
from karaoke_decide.voice_session.select import CandidateSong, SessionState, Warmth, choose_next
from karaoke_decide.voice_session.stopping import DoneReason, ResultSummary, check, summary
from karaoke_decide.voice_session.weights import EPS_DIFFICULTY, EPS_POSITION

SimPrior = Literal["self_register", "uniform"]

_POSITIONS: tuple[Position, ...] = ("too_low", "comfortable", "too_high", "both_ends")
_DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "stretch", "couldnt")
_KNEW = ("well", "partly", "barely")
_STRATA = ("pop", "rock", "rnb", "country", "musical", "dance")
_TYPES = list(VOICE_TYPES)
CONFIDENT_WITHIN = 10


@dataclass(frozen=True)
class SimResult:
    attempts: int
    done_reason: DoneReason
    summary: ResultSummary


@dataclass
class _RegisterProfile:
    """A 3c onboarding profile with only a self-reported register."""

    register: str | None
    method: str = "self"
    voice_type: str | None = None
    voice_type_between: str | None = None


def starting_posterior(true_h: Hyp, prior: SimPrior) -> Posterior:
    """'self_register': the prior a 3c register-only self report gives; 'uniform': flat."""
    if prior == "uniform":
        return Posterior.uniform()
    lo, hi = VOICE_TYPES[true_h[0]]
    return Posterior.from_profile(_RegisterProfile(register=_register_band((lo + hi) / 2)))


def synthetic_pool(rng: random.Random, n: int = 300) -> list[CandidateSong]:
    """Songs with p50 uniform in 45–76 and a p10–p90 span of 8–20; extremes 3 semitones beyond."""
    pool = []
    for i in range(n):
        p50 = rng.uniform(45, 76)
        half = rng.uniform(8, 20) / 2
        p10, p90 = p50 - half, p50 + half
        loved = rng.random() < 0.05
        pool.append(
            CandidateSong(
                song_key=f"syn{i:04d}",
                artist_key=f"art{i % 100:03d}",
                demand=SongDemand(low=p10 - 3, high=p90 + 3, p10=p10, p50=p50, p90=p90),
                stratum=rng.choice(_STRATA),
                know=1.0 if loved else rng.choice((1.0, 0.7, 0.4)),
                pref=rng.random(),
                popularity=rng.randint(0, 100),
                loved=loved,
            )
        )
    return pool


def _sample(rng: random.Random, options: tuple[str, ...], model_p: list[float], eps: float, noise: float) -> str:
    """Sample from the model's likelihood with its EPS floor replaced by `noise` (uniform answers)."""
    if rng.random() < noise:
        return rng.choice(options)
    clean = [max(0.0, (p - eps / len(options)) / (1 - eps)) for p in model_p]
    return rng.choices(options, weights=clean)[0]


def answer(rng: random.Random, true_h: Hyp, song: SongDemand, noise: float) -> tuple[Position, Difficulty]:
    """A simulated singer's (position, difficulty) answers for one song (sung at likely_octave)."""
    o = likely_octave(true_h, song)
    pos_p = [likelihood_position(true_h, song, a, o) for a in _POSITIONS]
    diff_p = [likelihood_difficulty(true_h, song, a, o) for a in _DIFFICULTIES]
    position = _sample(rng, _POSITIONS, pos_p, EPS_POSITION, noise)
    difficulty = _sample(rng, _DIFFICULTIES, diff_p, EPS_DIFFICULTY, noise)
    return position, difficulty  # type: ignore[return-value]


def simulate(
    true_h: Hyp,
    pool: list[CandidateSong],
    rng: random.Random,
    *,
    noise: float = 0.15,
    p_knew: tuple[float, float, float] = (0.6, 0.3, 0.1),
    warmth_path: tuple[Warmth, ...] = ("cold", "bit_warm", "warm"),
    prior: SimPrior = "self_register",
) -> SimResult:
    """Run one session; warmth_path[i] is the singer's warmth at attempt i (the last value persists)."""
    by_key = {c.song_key: c for c in pool}
    post = starting_posterior(true_h, prior)
    state = SessionState(
        posterior=post,
        warmth=warmth_path[0],
        attempts=0,
        range_attempts=0,
        tired_count=0,
        comfort_history=[post.comfort_estimate()],
    )
    while (done := check(state)) is None:
        pick = choose_next(state, pool)
        if pick is None:
            done = "no_candidates"
            break
        cand = by_key[pick.song_key]
        position, difficulty = answer(rng, true_h, cand.demand, noise)
        quiz = Quiz(
            feel="fine",
            position=position,
            difficulty=difficulty,
            knew=rng.choices(_KNEW, weights=p_knew)[0],  # type: ignore[arg-type]
            would_sing="yes",
            would_work_if=frozenset(),
            voice_now=None,
            version_quality=None,
            version_issues=frozenset(),
            tags=frozenset(),
        )
        ctx = Context(posture="standing", warmth=state.warmth, headphones=False)
        state.posterior = state.posterior.update(Evidence(cand.demand, quiz, ctx, None, stopped_early=False))
        state.attempts += 1
        state.range_attempts += 1
        state.done_keys.add(cand.song_key)
        state.recent_strata.append(cand.stratum)
        state.comfort_history.append(state.posterior.comfort_estimate())
        state.warmth = warmth_path[min(state.attempts, len(warmth_path) - 1)]
    return SimResult(attempts=state.attempts, done_reason=done, summary=summary(state, done))


@dataclass
class PopulationStats:
    n: int
    confident_within_10: float
    adjacent: float
    exact: float
    mean_attempts: float
    results: list[tuple[Hyp, SimResult]] = field(repr=False)

    def describe(self) -> str:
        return (
            f"n={self.n} confident≤{CONFIDENT_WITHIN}={self.confident_within_10:.1%} "
            f"equal-or-adjacent={self.adjacent:.1%} exact={self.exact:.1%} mean_attempts={self.mean_attempts:.2f}"
        )


def run_population(
    rng: random.Random, n_users: int = 200, prior: SimPrior = "self_register", pool_size: int = 300
) -> PopulationStats:
    """n_users singers cycling over every type × shift; a fresh synthetic pool for each cycle."""
    results: list[tuple[Hyp, SimResult]] = []
    pool: list[CandidateSong] = []
    for i in range(n_users):
        if i % len(HYPOTHESES) == 0:
            pool = synthetic_pool(rng, pool_size)
        h = HYPOTHESES[i % len(HYPOTHESES)]
        results.append((h, simulate(h, pool, rng, prior=prior)))

    def rate(ok: list[bool]) -> float:
        return sum(ok) / len(ok)

    def distance(h: Hyp, r: SimResult) -> int:
        return abs(_TYPES.index(h[0]) - _TYPES.index(r.summary.voice_type))

    return PopulationStats(
        n=len(results),
        confident_within_10=rate([r.done_reason == "confident" and r.attempts <= CONFIDENT_WITHIN for _, r in results]),
        adjacent=rate([distance(h, r) <= 1 for h, r in results]),
        exact=rate([distance(h, r) == 0 for h, r in results]),
        mean_attempts=sum(r.attempts for _, r in results) / len(results),
        results=results,
    )
