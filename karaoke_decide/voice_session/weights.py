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
W_PITCH_OPEN = 1.0
# Tessitura (model.likelihood_tessitura): where a song rated comfortable was actually sung, against each
# hypothesis's comfortable band. TESS_TEMP softens the band edges; TESS_SD is how far (semitones) the sung
# median may sit from the band's centre.
W_TESSITURA = 1.0
TESS_TEMP = 1.0
TESS_SD = 3.0
# A held top above a hypothesis's full range may be a falsetto note the app didn't catch (the sim leaks one into
# 30% of songs; Andrew's song 3): the upper bound of model.likelihood_pitch never drops below this.
HELD_HIGH_FALSETTO_P = 0.3
K_KNEW: dict[str | None, float] = {"well": 1.0, "partly": 0.7, "barely": 0.3, None: 0.8}
K_WARM_MISS = {"cold": 0.5, "bit_warm": 0.8, "warm": 1.0}
K_TIRED_MISS = 0.5
K_POSTURE_MISS = {"standing": 1.0, "sitting": 0.9, "lying": 0.7}
K_CONDITIONAL_MISS = 0.3
K_EARLY_MISS = 0.7
EPS_POSITION = 0.15
EPS_DIFFICULTY = 0.2
TEMP = 1.5
# Range-shaped prior from a measured profile (model.from_profile, method mic / session with a comfort band):
# w(h) = PRIOR_FLOOR + (1 − PRIOR_FLOOR)·exp(−(centre(h) − (profile centre + MIC_BIAS))² / (2·PRIOR_SD²)).
# The mic test reads low on average (Andrew's Pixel: bass from a baritone), so its centre is moved up; the floor
# keeps a wrong reading recoverable within a few songs.
PRIOR_FLOOR = 0.25
PRIOR_SD = 3.0
MIC_BIAS: dict[str, float] = {"mic": 1.0, "session": 0.0}
# Base lean over the octave a singer used when no pitch tells us (own octave most likely); model.py
# multiplies it by how well the song fits each hypothesis at that octave.
OCTAVE_PRIOR: dict[int, float] = {0: 0.6, -12: 0.2, 12: 0.2}

# Next-song choice (select.choose_next): score = like·know·((1 − SEP_WEIGHT)·EIG + SEP_WEIGHT·separation);
# like = LIKE_BASE + (1 − LIKE_BASE)·max(pref, 1 if loved), so an unliked song keeps LIKE_BASE of its score.
SEP_WEIGHT = 0.3
LIKE_BASE = 0.6

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


def evidence_weight(ev: Evidence, kind: Literal["quiz", "pitch", "tessitura"]) -> float:
    """Exponent applied to this evidence's log-likelihood in the posterior update."""
    knew = K_KNEW[ev.quiz.knew]
    if kind == "tessitura":
        return W_TESSITURA * knew
    if kind == "pitch":
        return (W_PITCH_HEADPHONES if ev.ctx.headphones else W_PITCH_OPEN) * knew
    return W_QUIZ * knew * (_miss_factor(ev) if is_miss(ev) else 1.0)
