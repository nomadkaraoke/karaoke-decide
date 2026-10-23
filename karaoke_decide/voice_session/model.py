"""Voice model: a posterior over (voice type, register shift) updated from per-song evidence.

Pure Python. Each song tried yields quiz answers (position, difficulty) and optionally an
on-device pitch summary; these become likelihoods per hypothesis (marginalised over the
octave sung when pitch doesn't tell us), weighted by context (see ``weights.py``) and
folded into log-probabilities, renormalised with log-sum-exp.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from karaoke_decide.vocal_range.notes import VOICE_TYPES
from karaoke_decide.voice_session.weights import (
    EPS_DIFFICULTY,
    EPS_POSITION,
    OCTAVE_PRIOR,
    TEMP,
    evidence_weight,
)

SHIFTS: tuple[int, ...] = (-2, -1, 0, 1, 2)
COMFORT_MARGIN = 5

Hyp = tuple[str, int]
HYPOTHESES: list[Hyp] = [(t, s) for t in VOICE_TYPES for s in SHIFTS]

Position = Literal["too_low", "comfortable", "too_high", "both_ends"]
Difficulty = Literal["easy", "stretch", "couldnt"]
_POSITIONS: tuple[Position, ...] = ("too_low", "comfortable", "too_high", "both_ends")
_DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "stretch", "couldnt")

MIN_VOICED_S = 20.0
MIN_HELD_FOR_PITCH = 2
_OCTAVES = (0, -12, 12)
_OCTAVE_PENALTY = 0.5
_LOGIT_CLAMP = 35.0  # σ(±35) stays strictly inside (0, 1) in float64
# Floor on normalised log-probabilities so exp() never underflows to 0.0 (float64 underflows near -745).
# Only reached after overwhelming contradictory evidence; it keeps every hypothesis representable.
LOG_P_FLOOR = -700.0


def comfort(h: Hyp) -> tuple[int, int]:
    lo, hi = VOICE_TYPES[h[0]]
    return lo + COMFORT_MARGIN + h[1], hi - COMFORT_MARGIN + h[1]


def full(h: Hyp) -> tuple[int, int]:
    lo, hi = VOICE_TYPES[h[0]]
    return lo + h[1], hi + h[1]


@dataclass(frozen=True)
class SongDemand:
    low: float
    high: float
    p10: float
    p50: float
    p90: float


@dataclass(frozen=True)
class Context:
    posture: Literal["standing", "sitting", "lying"]
    warmth: Literal["cold", "bit_warm", "warm"]
    headphones: bool


@dataclass(frozen=True)
class Quiz:
    feel: Literal["loved", "fine", "not_for_me"]
    position: Position
    difficulty: Difficulty | None
    knew: Literal["well", "partly", "barely"] | None
    would_sing: Literal["yes", "yes_other_key", "no"] | None
    would_work_if: frozenset[str]
    voice_now: Literal["fresh", "warm", "tired"] | None
    version_quality: Literal["good", "not_great"] | None
    version_issues: frozenset[str]
    tags: frozenset[str]


@dataclass(frozen=True)
class PitchSummary:
    voiced_s: float
    held_count: float
    held_low: float
    held_high: float
    p10: float
    p50: float
    p90: float


@dataclass(frozen=True)
class Evidence:
    song: SongDemand
    quiz: Quiz
    ctx: Context
    pitch: PitchSummary | None
    stopped_early: bool


class VoiceProfileLike(Protocol):
    """The fields of 3c's ``VoiceProfileOut`` the prior needs.

    The self-reported register is read by ``_profile_register``: ``VoiceProfileOut`` keeps it on
    ``register_`` (values lower / middle / higher); simple stand-ins may use ``register`` (low / mid / high).
    """

    @property
    def method(self) -> str: ...  # 'mic' | 'self' | other

    @property
    def voice_type(self) -> str | None: ...

    @property
    def voice_type_between(self) -> str | None: ...  # the adjacent type when reported "between"


def _sigmoid(x: float) -> float:
    x = max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, x))
    return 1.0 / (1.0 + math.exp(-x))


def usable_pitch(pitch: PitchSummary | None) -> bool:
    return pitch is not None and pitch.voiced_s >= MIN_VOICED_S and pitch.held_count >= MIN_HELD_FOR_PITCH


def sung_octave(song: SongDemand, pitch: PitchSummary | None) -> int | None:
    """The octave the singer actually sang in, from the pitch median; None if unknowable."""
    if pitch is None or pitch.voiced_s < MIN_VOICED_S:
        return None
    return min((-12, 0, 12), key=lambda o: abs(pitch.p50 - (song.p50 + o)))


def _demands(h: Hyp, song: SongDemand, o: int) -> tuple[float, float, float, float]:
    """(d_high, d_low, x_high, x_low): semitones the song exceeds comfort/full range at octave o."""
    c_lo, c_hi = comfort(h)
    f_lo, f_hi = full(h)
    return (
        song.p90 + o - c_hi,
        c_lo - (song.p10 + o),
        song.high + o - f_hi,
        f_lo - (song.low + o),
    )


def likely_octave(h: Hyp, song: SongDemand) -> int:
    """The single octave hypothesis h would most plausibly sing this song in (0 preferred by 0.5 st).

    Only for places that must *pick* one octave (e.g. expected stretch, reason labels). Likelihoods
    never use it: under argmin, h and h+12 (baritone/mezzo, tenor/soprano) were exactly symmetric,
    so quiz-only answers could not tell them apart. Likelihoods marginalise over a fit-weighted prior instead.
    """

    def cost(o: int) -> float:
        d_high, d_low, _, _ = _demands(h, song, o)
        return max(d_low, d_high) + (0.0 if o == 0 else _OCTAVE_PENALTY)

    return min(_OCTAVES, key=cost)


def _octave_mixture(h: Hyp, song: SongDemand, octave: int | None) -> tuple[tuple[int, float], ...]:
    """Weights over the octave h sang the song in.

    Known octave -> that octave alone. Unknown -> w(o | h, song) ∝ OCTAVE_PRIOR[o]·σ(−(max(d_low, d_high) − 1)/TEMP):
    each hypothesis is assumed to have mostly sung where the song fits it, with a base lean to the own octave.
    A fixed prior (no fit term) let the song mix's octave decide between h and h+12 (e.g. ~5×/song toward mezzo
    for a baritone on female-range songs). The result is an honest near-tie between h and h+12 under quiz-only
    evidence; that is intended — separating them is the job of the profile prior or pitch.
    """
    if octave is not None:
        return ((octave, 1.0),)
    raw: list[tuple[int, float]] = []
    for o, base in OCTAVE_PRIOR.items():
        d_high, d_low, _, _ = _demands(h, song, o)
        raw.append((o, base * _sigmoid(-(max(d_low, d_high) - 1) / TEMP)))
    z = sum(w for _, w in raw)
    return tuple((o, w / z) for o, w in raw)


def _position_at(h: Hyp, song: SongDemand, answer: Position, o: int) -> float:
    d_high, d_low, _, _ = _demands(h, song, o)
    hi = _sigmoid((d_high - 1) / TEMP)
    lo = _sigmoid((d_low - 1) / TEMP)
    raw = {
        "too_high": hi * (1 - lo),
        "too_low": lo * (1 - hi),
        "both_ends": hi * lo,
        "comfortable": (1 - hi) * (1 - lo),
    }
    return (1 - EPS_POSITION) * raw[answer] / sum(raw.values()) + EPS_POSITION / len(_POSITIONS)


def _difficulty_at(h: Hyp, song: SongDemand, answer: Difficulty, o: int) -> float:
    d_high, d_low, x_high, x_low = _demands(h, song, o)
    x = _sigmoid((max(x_high, x_low) - 0.5) / 1.0)
    d = _sigmoid((max(d_high, d_low) - 0.5) / 1.0)
    raw = {"couldnt": x, "stretch": d * (1 - x), "easy": (1 - d) * (1 - x)}
    return (1 - EPS_DIFFICULTY) * raw[answer] / sum(raw.values()) + EPS_DIFFICULTY / len(_DIFFICULTIES)


def likelihood_position(h: Hyp, song: SongDemand, answer: Position, octave: int | None) -> float:
    """P(position answer | h): at the sung octave if known, else Σ_o w(o | h, song)·P(answer | h, o)."""
    return sum(w * _position_at(h, song, answer, o) for o, w in _octave_mixture(h, song, octave))


def likelihood_difficulty(h: Hyp, song: SongDemand, answer: Difficulty, octave: int | None) -> float:
    """P(difficulty answer | h): at the sung octave if known, else marginalised over w(o | h, song)."""
    return sum(w * _difficulty_at(h, song, answer, o) for o, w in _octave_mixture(h, song, octave))


def likelihood_pitch(h: Hyp, pitch: PitchSummary | None) -> float:
    """Held notes are achieved notes: they bound the full range and are never a "miss".

    Returns 1.0 (no information) when the pitch summary is absent or too thin to use.
    """
    if pitch is None or not usable_pitch(pitch):
        return 1.0
    f_lo, f_hi = full(h)
    return _sigmoid((f_hi - pitch.held_high + 1) / 1.0) * _sigmoid((pitch.held_low - f_lo + 1) / 1.0)


def _logsumexp(values: list[float]) -> float:
    m = max(values)
    return m + math.log(sum(math.exp(v - m) for v in values))


def _normalised(log_w: dict[Hyp, float]) -> dict[Hyp, float]:
    z = _logsumexp(list(log_w.values()))
    floored = {h: max(v - z, LOG_P_FLOOR) for h, v in log_w.items()}
    z2 = _logsumexp(list(floored.values()))  # ~0 unless the floor was hit
    return {h: v - z2 for h, v in floored.items()}


# Prior weights by profile method: (named type, adjacent/"between" type, others).
_PRIOR_WEIGHTS = {"mic": (4.0, 2.0, 1.0), "self": (3.0, 1.5, 1.0)}
_REGISTER_WEIGHT = 2.0


def _register_band(centre: float) -> str:
    """Provisional mapping of a type's range centre to a self-reported register band."""
    if centre < 57:
        return "low"
    if centre <= 62:
        return "mid"
    return "high"


_REGISTERS = {"lower": "low", "middle": "mid", "higher": "high", "low": "low", "mid": "mid", "high": "high"}


def _profile_register(profile: VoiceProfileLike) -> str | None:
    """'low' | 'mid' | 'high' from ``register_`` (3c VoiceProfileOut), else ``register``.

    Only strings count: on a pydantic model ``register`` is ABCMeta's classmethod, not a field.
    """
    for attr in ("register_", "register"):
        value = getattr(profile, attr, None)
        if isinstance(value, str):
            return _REGISTERS.get(value)
    return None


def _type_prior(profile: VoiceProfileLike | None) -> dict[str, float]:
    types = list(VOICE_TYPES)
    weights = dict.fromkeys(types, 1.0)
    if profile is None:
        return weights
    if profile.voice_type in VOICE_TYPES and profile.method in _PRIOR_WEIGHTS:
        named, near, other = _PRIOR_WEIGHTS[profile.method]
        weights = dict.fromkeys(types, other)
        if profile.voice_type_between in VOICE_TYPES:
            neighbours = [profile.voice_type_between]
        else:
            i = types.index(profile.voice_type)
            neighbours = [types[j] for j in (i - 1, i + 1) if 0 <= j < len(types)]
        for t in neighbours:
            weights[t] = near
        weights[profile.voice_type] = named
    elif profile.method == "self" and profile.voice_type is None and (register := _profile_register(profile)):
        for t, (lo, hi) in VOICE_TYPES.items():
            if _register_band((lo + hi) / 2) == register:
                weights[t] = _REGISTER_WEIGHT
    return weights


@dataclass(frozen=True)
class Posterior:
    """Normalised log-probabilities over HYPOTHESES."""

    log_p: dict[Hyp, float]

    @classmethod
    def uniform(cls) -> Posterior:
        return cls(_normalised(dict.fromkeys(HYPOTHESES, 0.0)))

    @classmethod
    def from_profile(cls, profile: VoiceProfileLike | None) -> Posterior:
        """Prior from a stored voice profile; shifts are uniform within each type."""
        tw = _type_prior(profile)
        return cls(_normalised({h: math.log(tw[h[0]]) for h in HYPOTHESES}))

    def update(self, ev: Evidence) -> Posterior:
        octave = sung_octave(ev.song, ev.pitch)
        w_quiz = evidence_weight(ev, "quiz")
        use_pitch = usable_pitch(ev.pitch)
        w_pitch = evidence_weight(ev, "pitch") if use_pitch else 0.0
        new: dict[Hyp, float] = {}
        for h, lp in self.log_p.items():
            ll = math.log(likelihood_position(h, ev.song, ev.quiz.position, octave))
            if ev.quiz.difficulty is not None:
                ll += math.log(likelihood_difficulty(h, ev.song, ev.quiz.difficulty, octave))
            total = lp + w_quiz * ll
            if use_pitch:
                total += w_pitch * math.log(likelihood_pitch(h, ev.pitch))
            new[h] = total
        return Posterior(_normalised(new))

    def probs(self) -> dict[Hyp, float]:
        return {h: math.exp(v) for h, v in self.log_p.items()}

    def type_marginal(self) -> dict[str, float]:
        marg = dict.fromkeys(VOICE_TYPES, 0.0)
        for (t, _), p in self.probs().items():
            marg[t] += p
        return marg

    def _mean_bounds(self, bounds: dict[Hyp, tuple[int, int]]) -> tuple[float, float]:
        probs = self.probs()
        return (
            sum(p * bounds[h][0] for h, p in probs.items()),
            sum(p * bounds[h][1] for h, p in probs.items()),
        )

    def comfort_estimate(self) -> tuple[float, float]:
        """Posterior mean of the comfortable-range bounds (MIDI)."""
        return self._mean_bounds({h: comfort(h) for h in self.log_p})

    def full_estimate(self) -> tuple[float, float]:
        """Posterior mean of the full-range bounds (MIDI)."""
        return self._mean_bounds({h: full(h) for h in self.log_p})
