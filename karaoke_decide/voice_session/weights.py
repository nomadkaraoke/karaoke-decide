"""Context-weighted evidence: how much one song's answers should move the voice posterior.

Misses (too high/low, couldn't finish) under poor conditions (cold voice, tired, lying down,
"would work if warmer/practised", stopped early) count for less; successes are never discounted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from karaoke_decide.voice_session.model import Evidence

W_QUIZ = 1.0
W_PITCH_HEADPHONES = 1.5
W_PITCH_OPEN = 0.6
K_KNEW: dict[str | None, float] = {"well": 1.0, "partly": 0.7, "barely": 0.3, None: 0.8}
K_WARM_MISS = {"cold": 0.5, "bit_warm": 0.8, "warm": 1.0}
K_TIRED_MISS = 0.5
K_POSTURE_MISS = {"standing": 1.0, "sitting": 0.9, "lying": 0.7}
K_CONDITIONAL_MISS = 0.3
K_EARLY_MISS = 0.7
EPS_POSITION = 0.15
EPS_DIFFICULTY = 0.2
TEMP = 1.5

CONDITIONAL_REASONS = frozenset({"warmer", "practice"})


def is_miss(ev: Evidence) -> bool:
    """A song that didn't fit: wrong position or couldn't finish."""
    return ev.quiz.position != "comfortable" or ev.quiz.difficulty == "couldnt"


def _miss_factor(ev: Evidence) -> float:
    q, c = ev.quiz, ev.ctx
    k = K_WARM_MISS[c.warmth] * K_POSTURE_MISS[c.posture]
    if q.voice_now == "tired":
        k *= K_TIRED_MISS
    if q.would_work_if & CONDITIONAL_REASONS:
        k *= K_CONDITIONAL_MISS
    if ev.stopped_early:
        k *= K_EARLY_MISS
    return k


def evidence_weight(ev: Evidence, kind: Literal["quiz", "pitch"]) -> float:
    """Exponent applied to this evidence's log-likelihood in the posterior update."""
    knew = K_KNEW[ev.quiz.knew]
    if kind == "pitch":
        return (W_PITCH_HEADPHONES if ev.ctx.headphones else W_PITCH_OPEN) * knew
    return W_QUIZ * knew * (_miss_factor(ev) if is_miss(ev) else 1.0)
