"""Simulated singers for checking that selection + model + stopping converge on the true voice type.

A simulated singer with true hypothesis h sings each offered song in the octave h would most plausibly
use (likely_octave) and answers the position and difficulty questions by sampling the model's own
likelihoods at that octave, with `noise` as the chance of a uniformly random answer.

Priors (``SimPrior``): "self_register" (a 3c register-only self report), "uniform", or "mic" (the 3c mic test:
measured comfort centre = true centre + N(MIC_BIAS_MEAN, MIC_BIAS_SD), saved as an 11-key band, classified with
the 3c centre rule). Pitch (``SimPitch``): "none", or an on-device summary at the sung octave with headphones
(σ 0.7) or an open mic (σ 1.5), including a falsetto note leaking into ``held_high`` with FALSETTO_LEAK_P.
Each run keeps a ``trace``: the result after every attempt, so a population can be scored at k songs.

Answer styles (``AnswerStyle``): "model" answers from the model's own likelihoods (the original sim; the existing
gates use it). "realistic" is what real singers do (Andrew answered "comfortable" 4/4 on songs that reached past
his comfortable band): a song whose sung body (p10..p90 at the sung octave) stays inside the singer's FULL chest
range is answered "comfortable" with REALISTIC_COMFORT_P, otherwise (and for every song that leaves the full
range) the model-likelihood answer. Difficulty is always the model-likelihood answer. The E5 gates use it.
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
    PitchSummary,
    Position,
    Posterior,
    Quiz,
    SongDemand,
    _register_band,
    comfort,
    full,
    likelihood_difficulty,
    likelihood_position,
    likely_octave,
)
from karaoke_decide.voice_session.select import CandidateSong, SessionState, Warmth, choose_next
from karaoke_decide.voice_session.stopping import DoneReason, ResultSummary, check, summary
from karaoke_decide.voice_session.weights import EPS_DIFFICULTY, EPS_POSITION

SimPrior = Literal["self_register", "uniform", "mic"]
SimPitch = Literal["none", "headphones", "open"]
AnswerStyle = Literal["model", "realistic"]

_POSITIONS: tuple[Position, ...] = ("too_low", "comfortable", "too_high", "both_ends")
_DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "stretch", "couldnt")
_KNEW = ("well", "partly", "barely")
_STRATA = ("pop", "rock", "rnb", "country", "musical", "dance")
_TYPES = list(VOICE_TYPES)
CONFIDENT_WITHIN = 10
KS: tuple[int, ...] = (3, 4, 5, 10)  # song budgets PopulationStats scores

# The 3c mic test as a prior: the free-sing centre reads low (Andrew's Pixel: bass from a baritone).
MIC_BIAS_MEAN = -1.0
MIC_BIAS_SD = 2.5
MIN_COMFORT_KEYS = 11  # nomad-mobile #20: the saved comfort band, keys counted inclusively
# Simulated on-device pitch summaries.
PITCH_SD: dict[str, float] = {"headphones": 0.7, "open": 1.5}
HELD_MARGIN = 1.0  # held notes reach 1 semitone past the sung p10/p90
VOICED_S = (60.0, 180.0)
FALSETTO_LEAK_P = 0.3
FALSETTO_ABOVE = 3  # a leaked falsetto note sits this far above the chest (full) top
# Realistic singers: P("comfortable") for a song whose p10..p90 (at the sung octave) is inside the full range.
REALISTIC_COMFORT_P = 0.8


@dataclass(frozen=True)
class SimResult:
    attempts: int
    done_reason: DoneReason
    summary: ResultSummary
    trace: tuple[ResultSummary, ...] = ()  # the result after each attempt

    def at(self, k: int) -> ResultSummary:
        """The result the singer would see after k songs (the final one if the session stopped sooner)."""
        return self.trace[min(k, len(self.trace)) - 1] if self.trace else self.summary


@dataclass
class _RegisterProfile:
    """A 3c onboarding profile with only a self-reported register."""

    register: str | None
    method: str = "self"
    voice_type: str | None = None
    voice_type_between: str | None = None


@dataclass(frozen=True)
class _MicProfile:
    """A 3c mic-test profile (the fields ``Posterior.from_profile`` reads, plus the saved band)."""

    voice_type: str
    voice_type_between: str | None
    confidence: str
    low_midi: int
    high_midi: int
    comfort_low: int
    comfort_high: int
    method: str = "mic"


def classify_3c(low: float, high: float, comfort_low: float, comfort_high: float) -> tuple[str, str | None, float]:
    """(type, between, margin): nomad-mobile ``classifyVoice`` (the 3c centre rule).

    score = 1 − (0.65·|comfort centre − type centre|/6 + 0.35·(range outside the type)/12); "between" the
    runner-up when adjacent and the margin is under 0.08."""
    cc = (comfort_low + comfort_high) / 2
    scored = []
    for i, (t, (lo, hi)) in enumerate(VOICE_TYPES.items()):
        outside = max(0.0, lo - low) + max(0.0, high - hi)
        scored.append((1 - (0.65 * abs(cc - (lo + hi) / 2) / 6 + 0.35 * outside / 12), -i, t, i))
    ranked = sorted(scored, reverse=True)
    (s1, _, best, i1), (s2, _, runner, i2) = ranked[0], ranked[1]
    margin = s1 - s2
    return best, runner if abs(i1 - i2) == 1 and margin < 0.08 else None, margin


def mic_profile(true_h: Hyp, rng: random.Random) -> _MicProfile:
    """The 3c mic test for a singer with true hypothesis h: one N(MIC_BIAS_MEAN, MIC_BIAS_SD) error on the
    whole reading (centre and range), comfort saved as an 11-key band around the measured centre."""
    c_lo, c_hi = comfort(true_h)
    f_lo, f_hi = full(true_h)
    err = rng.gauss(MIC_BIAS_MEAN, MIC_BIAS_SD)
    low, high = round(f_lo + err), round(f_hi + err)
    band_lo = round((c_lo + c_hi) / 2 + err - (MIN_COMFORT_KEYS - 1) / 2)
    band_lo = max(low, min(band_lo, high - (MIN_COMFORT_KEYS - 1)))
    band_hi = band_lo + MIN_COMFORT_KEYS - 1
    voice_type, between, margin = classify_3c(low, high, band_lo, band_hi)
    return _MicProfile(
        voice_type=voice_type,
        voice_type_between=between,
        confidence="high" if margin >= 0.1 else "medium",  # a clean mic test: enough held notes and span
        low_midi=low,
        high_midi=high,
        comfort_low=band_lo,
        comfort_high=band_hi,
    )


def starting_posterior(true_h: Hyp, prior: SimPrior, rng: random.Random | None = None) -> Posterior:
    """'self_register': the prior a 3c register-only self report gives; 'uniform': flat; 'mic': the 3c mic
    test (``mic_profile``, drawn from ``rng``)."""
    if prior == "uniform":
        return Posterior.uniform()
    if prior == "mic":
        if rng is None:
            raise ValueError("the mic prior needs an rng")
        return Posterior.from_profile(mic_profile(true_h, rng))
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


def answer(
    rng: random.Random, true_h: Hyp, song: SongDemand, noise: float, style: AnswerStyle = "model"
) -> tuple[Position, Difficulty]:
    """A simulated singer's (position, difficulty) answers for one song (sung at likely_octave).

    "model": both sampled from the model's likelihoods. "realistic": the same, except that a song whose p10..p90
    stays inside the full range is answered "comfortable" with REALISTIC_COMFORT_P (one extra draw, only then)."""
    o = likely_octave(true_h, song)
    pos_p = [likelihood_position(true_h, song, a, o) for a in _POSITIONS]
    diff_p = [likelihood_difficulty(true_h, song, a, o) for a in _DIFFICULTIES]
    position = _sample(rng, _POSITIONS, pos_p, EPS_POSITION, noise)
    difficulty = _sample(rng, _DIFFICULTIES, diff_p, EPS_DIFFICULTY, noise)
    if style == "realistic":
        f_lo, f_hi = full(true_h)
        if f_lo <= song.p10 + o and song.p90 + o <= f_hi and rng.random() < REALISTIC_COMFORT_P:
            position = "comfortable"
    return position, difficulty  # type: ignore[return-value]


def sim_pitch(rng: random.Random, true_h: Hyp, song: SongDemand, pitch: SimPitch) -> PitchSummary | None:
    """The on-device pitch summary of true_h singing ``song`` at its likely octave (None for "none").

    Percentiles: the song's, clipped into the chest (full) range, plus N(0, PITCH_SD) each (then sorted).
    Held notes: the clipped p10/p90 ∓ HELD_MARGIN, inside the chest range, except that with FALSETTO_LEAK_P a
    falsetto note FALSETTO_ABOVE over the chest top leaks into ``held_high``."""
    if pitch == "none":
        return None
    sd = PITCH_SD[pitch]
    o = likely_octave(true_h, song)
    f_lo, f_hi = full(true_h)

    def clip(x: float) -> float:
        return min(float(f_hi), max(float(f_lo), x))

    c10, c50, c90 = clip(song.p10 + o), clip(song.p50 + o), clip(song.p90 + o)
    p10, p50, p90 = sorted(c + rng.gauss(0, sd) for c in (c10, c50, c90))
    held_low = max(float(f_lo), c10 - HELD_MARGIN)
    held_high = min(float(f_hi), c90 + HELD_MARGIN)
    if rng.random() < FALSETTO_LEAK_P:
        held_high = float(f_hi + FALSETTO_ABOVE)
    voiced_s = rng.uniform(*VOICED_S)
    return PitchSummary(
        voiced_s=voiced_s,
        held_count=float(max(2, round(voiced_s / 3))),
        held_low=held_low,
        held_high=held_high,
        p10=p10,
        p50=p50,
        p90=p90,
    )


def simulate(
    true_h: Hyp,
    pool: list[CandidateSong],
    rng: random.Random,
    *,
    noise: float = 0.15,
    p_knew: tuple[float, float, float] = (0.6, 0.3, 0.1),
    warmth_path: tuple[Warmth, ...] = ("cold", "bit_warm", "warm"),
    prior: SimPrior = "self_register",
    pitch: SimPitch = "none",
    budget: int | None = None,
    answer_style: AnswerStyle = "model",
) -> SimResult:
    """Run one session; warmth_path[i] is the singer's warmth at attempt i (the last value persists).

    ``budget`` stops after that many songs ('max_attempts'), as a singer who gives up would."""
    by_key = {c.song_key: c for c in pool}
    post = starting_posterior(true_h, prior, rng)
    state = SessionState(
        posterior=post,
        warmth=warmth_path[0],
        attempts=0,
        range_attempts=0,
        tired_count=0,
        comfort_history=[post.comfort_estimate()],
    )
    trace: list[ResultSummary] = []
    while (done := check(state)) is None:
        if budget is not None and state.attempts >= budget:
            done = "max_attempts"
            break
        pick = choose_next(state, pool)
        if pick is None:
            done = "no_candidates"
            break
        cand = by_key[pick.song_key]
        position, difficulty = answer(rng, true_h, cand.demand, noise, answer_style)
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
        ctx = Context(posture="standing", warmth=state.warmth, headphones=pitch == "headphones")
        sung = sim_pitch(rng, true_h, cand.demand, pitch)
        ev = Evidence(cand.demand, quiz, ctx, sung, stopped_early=False)
        state.posterior = state.posterior.update(ev)
        state.evidence.append(ev)
        state.attempts += 1
        state.range_attempts += 1
        state.done_keys.add(cand.song_key)
        state.recent_strata.append(cand.stratum)
        state.comfort_history.append(state.posterior.comfort_estimate())
        state.warmth = warmth_path[min(state.attempts, len(warmth_path) - 1)]
        trace.append(summary(state))
    return SimResult(attempts=state.attempts, done_reason=done, summary=summary(state, done), trace=tuple(trace))


@dataclass
class PopulationStats:
    n: int
    confident_within_10: float
    adjacent: float
    exact: float
    mean_attempts: float
    results: list[tuple[Hyp, SimResult]] = field(repr=False)
    # Scored on the result after k songs (SimResult.at(k)), for k in KS. confident_within[k]: stopped
    # 'confident' within k songs.
    exact_at: dict[int, float] = field(default_factory=dict)
    adjacent_at: dict[int, float] = field(default_factory=dict)
    confident_within: dict[int, float] = field(default_factory=dict)
    # Stopped 'confident' (medium / high) within k songs with the wrong exact type.
    confidently_wrong_within: dict[int, float] = field(default_factory=dict)

    def describe(self) -> str:
        at = " ".join(
            f"exact@{k}={self.exact_at[k]:.0%}/adj@{k}={self.adjacent_at[k]:.0%}/conf≤{k}={self.confident_within[k]:.0%}"
            f"/wrong≤{k}={self.confidently_wrong_within.get(k, 0.0):.0%}"
            for k in sorted(self.exact_at)
        )
        return (
            f"n={self.n} confident≤{CONFIDENT_WITHIN}={self.confident_within_10:.1%} "
            f"equal-or-adjacent={self.adjacent:.1%} exact={self.exact:.1%} mean_attempts={self.mean_attempts:.2f}"
            + (f" | {at}" if at else "")
        )


def run_population(
    rng: random.Random,
    n_users: int = 200,
    prior: SimPrior = "self_register",
    pool_size: int = 300,
    *,
    pitch: SimPitch = "none",
    budget: int | None = None,
    answer_style: AnswerStyle = "model",
) -> PopulationStats:
    """n_users singers cycling over every type × shift; a fresh synthetic pool for each cycle."""
    results: list[tuple[Hyp, SimResult]] = []
    pool: list[CandidateSong] = []
    for i in range(n_users):
        if i % len(HYPOTHESES) == 0:
            pool = synthetic_pool(rng, pool_size)
        h = HYPOTHESES[i % len(HYPOTHESES)]
        results.append((h, simulate(h, pool, rng, prior=prior, pitch=pitch, budget=budget, answer_style=answer_style)))

    def rate(ok: list[bool]) -> float:
        return sum(ok) / len(ok)

    def distance(h: Hyp, s: ResultSummary) -> int:
        return abs(_TYPES.index(h[0]) - _TYPES.index(s.voice_type))

    return PopulationStats(
        n=len(results),
        confident_within_10=rate([r.done_reason == "confident" and r.attempts <= CONFIDENT_WITHIN for _, r in results]),
        adjacent=rate([distance(h, r.summary) <= 1 for h, r in results]),
        exact=rate([distance(h, r.summary) == 0 for h, r in results]),
        mean_attempts=sum(r.attempts for _, r in results) / len(results),
        results=results,
        exact_at={k: rate([distance(h, r.at(k)) == 0 for h, r in results]) for k in KS},
        adjacent_at={k: rate([distance(h, r.at(k)) <= 1 for h, r in results]) for k in KS},
        confident_within={k: rate([r.done_reason == "confident" and r.attempts <= k for _, r in results]) for k in KS},
        confidently_wrong_within={
            k: rate(
                [r.done_reason == "confident" and r.attempts <= k and distance(h, r.summary) > 0 for h, r in results]
            )
            for k in KS
        },
    )
