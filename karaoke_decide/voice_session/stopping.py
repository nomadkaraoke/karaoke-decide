"""Stopping rule and result summary (spec §10a, tuned in §10c / Phase 3d batch 1).

Done when the top voice type reaches TOP_THRESHOLD (or an adjacent pair PAIR_THRESHOLD, reported as
"between X and Y"; within MEDIUM_LEAN_UNTIL songs the top type must also lead with MEDIUM_MIN_TOP), the comfortable
range has settled (both bounds of the model's estimate moved at most STABLE_SEMITONES over the last 2 attempts,
or the comfort is measured from the songs rated comfortable, ``comfort.measured_comfort``), and at least
MIN_RANGE_ATTEMPTS attempts carried range evidence. Also done after "Getting tired" twice or MAX_ATTEMPTS
attempts; the caller sets 'no_candidates' when choose_next returns None.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from karaoke_decide.voice_session.comfort import comfort_band, measured_comfort
from karaoke_decide.voice_session.model import VOICE_TYPES, Evidence, comfort
from karaoke_decide.voice_session.select import SessionState

DoneReason = Literal["confident", "tired", "max_attempts", "no_candidates"]
ConfidenceLabel = Literal["low", "medium", "high"]
ImproveHint = Literal["sing_higher_warm", "sing_lower", "more_songs"]
ComfortSource = Literal["measured", "model"]

MAX_ATTEMPTS = 12
TOP_THRESHOLD = 0.75
PAIR_THRESHOLD = 0.85
STABLE_SEMITONES = 2
MIN_RANGE_ATTEMPTS = 3
TIRED_LIMIT = 2
STABLE_WINDOW = 3  # estimates before and after each of the last 2 attempts
HINT_SD = 2.0
# Early "between X and Y" stops need X to lead: adjacent types share ranges at the ends of their shifts
# (baritone+2 is tenor−1), so a near 50/50 pair is often the wrong type. After MEDIUM_LEAN_UNTIL songs a settled
# pair is the honest answer and stops.
MEDIUM_MIN_TOP = 0.7
MEDIUM_LEAN_UNTIL = 5

_TYPES = list(VOICE_TYPES)


@dataclass(frozen=True)
class ResultSummary:
    voice_type: str
    between: str | None
    top_p: float
    pair_p: float
    confidence_label: ConfidenceLabel
    comfort: tuple[int, int]
    full: tuple[int, int]
    improve_hint: ImproveHint | None
    comfort_source: ComfortSource = "model"  # "measured": from the songs rated comfortable (comfort.py)
    falsetto_high: int | None = None  # highest note labelled falsetto on a counted attempt (never chest range)


def _top(marg: dict[str, float]) -> tuple[str, float]:
    t = max(_TYPES, key=lambda x: marg[x])
    return t, marg[t]


def _best_pair(marg: dict[str, float]) -> tuple[tuple[str, str], float]:
    pairs = [(_TYPES[i], _TYPES[i + 1]) for i in range(len(_TYPES) - 1)]
    best = max(pairs, key=lambda pr: marg[pr[0]] + marg[pr[1]])
    return best, marg[best[0]] + marg[best[1]]


def _stable(history: list[tuple[float, float]]) -> bool:
    if len(history) < STABLE_WINDOW:
        return False
    window = history[-STABLE_WINDOW:]
    return all(max(b[i] for b in window) - min(b[i] for b in window) <= STABLE_SEMITONES for i in (0, 1))


def _enough_evidence(state: SessionState) -> bool:
    """Enough attempts with range evidence, and the comfortable range has settled: the model's estimate is
    stable, or the comfort shown is measured from the songs rated comfortable (it doesn't move with the type)."""
    if state.range_attempts < MIN_RANGE_ATTEMPTS:
        return False
    return _stable(state.comfort_history) or measured_comfort(state.evidence) is not None


def _posterior_label(marg: dict[str, float], counting: int) -> ConfidenceLabel:
    """``counting``: songs that count (excluded attempts don't), for the MEDIUM_LEAN_UNTIL window."""
    top = _top(marg)[1]
    if top >= TOP_THRESHOLD:
        return "high"
    if _best_pair(marg)[1] >= PAIR_THRESHOLD and (counting > MEDIUM_LEAN_UNTIL or top >= MEDIUM_MIN_TOP):
        return "medium"
    return "low"


def _label(state: SessionState) -> ConfidenceLabel:
    """Posterior confidence, capped at 'low' until the evidence gates of the confident stop are met."""
    marg = state.posterior.type_marginal()
    return _posterior_label(marg, len(state.evidence)) if _enough_evidence(state) else "low"


def check(state: SessionState) -> DoneReason | None:
    """Why the session is done, or None to keep going."""
    if _label(state) != "low":
        return "confident"
    if state.tired_count >= TIRED_LIMIT:
        return "tired"
    if state.attempts >= MAX_ATTEMPTS:
        return "max_attempts"
    return None


def comfort_sd(state: SessionState) -> tuple[float, float]:
    """The posterior SD of the model's comfort estimate (low, high): how unsure the type-mean band still is."""
    probs = state.posterior.probs()
    mean_lo, mean_hi = state.posterior.comfort_estimate()
    var_lo = sum(p * (comfort(h)[0] - mean_lo) ** 2 for h, p in probs.items())
    var_hi = sum(p * (comfort(h)[1] - mean_hi) ** 2 for h, p in probs.items())
    return math.sqrt(var_lo), math.sqrt(var_hi)


def summary(
    state: SessionState, done_reason: DoneReason | None = None, evidence: Sequence[Evidence] | None = None
) -> ResultSummary:
    """The result shown to the user (valid mid-session too, e.g. on Finish).

    ``done_reason`` is how the session ended when the caller knows it (e.g. 'no_candidates', which check()
    can't see); otherwise check(state) decides whether "more songs" is still a useful hint.
    Comfort is measured from the songs rated comfortable (``evidence``, default ``state.evidence``) when there
    are enough of them (``comfort.comfort_band``: at least 11 keys, covering each counted song, inside the
    full-range estimate); else the posterior mean.
    """
    post = state.posterior
    marg = post.type_marginal()
    voice_type, top_p = _top(marg)
    pair, pair_p = _best_pair(marg)
    label = _label(state)
    between = None
    if label == "medium":
        between = pair[1] if pair[0] == voice_type else pair[0]

    sd_lo, sd_hi = comfort_sd(state)
    hint: ImproveHint | None = None
    # SessionState carries the current warmth only; warmth never steps down in the app, so "not warm now"
    # stands in for "never warm".
    if sd_hi > HINT_SD and state.warmth != "warm":
        hint = "sing_higher_warm"
    elif sd_lo > HINT_SD:
        hint = "sing_lower"
    elif (done_reason or check(state)) is None:
        hint = "more_songs"

    f_lo, f_hi = round(post.full_estimate()[0]), round(post.full_estimate()[1])
    band = comfort_band(state.evidence if evidence is None else evidence, (f_lo, f_hi))
    source: ComfortSource = "model"
    if band is not None:
        source, comfort_band_ = "measured", band
    else:
        c_lo, c_hi = post.comfort_estimate()
        comfort_band_ = (round(c_lo), round(c_hi))
    return ResultSummary(
        voice_type=voice_type,
        between=between,
        top_p=top_p,
        pair_p=pair_p,
        confidence_label=label,
        comfort=comfort_band_,
        full=(f_lo, f_hi),
        improve_hint=hint,
        comfort_source=source,
        falsetto_high=round(state.falsetto_high) if state.falsetto_high is not None else None,
    )
