"""Stopping rule and result summary (spec §10a).

Done when the top voice type reaches TOP_THRESHOLD (or an adjacent pair PAIR_THRESHOLD, reported as
"between X and Y"), both comfortable bounds moved at most STABLE_SEMITONES over the last 2 attempts, and
at least MIN_RANGE_ATTEMPTS attempts carried range evidence. Also done after "Getting tired" twice or
MAX_ATTEMPTS attempts; the caller sets 'no_candidates' when choose_next returns None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from karaoke_decide.voice_session.model import VOICE_TYPES, comfort
from karaoke_decide.voice_session.select import SessionState

DoneReason = Literal["confident", "tired", "max_attempts", "no_candidates"]
ConfidenceLabel = Literal["low", "medium", "high"]
ImproveHint = Literal["sing_higher_warm", "sing_lower", "more_songs"]

MAX_ATTEMPTS = 12
TOP_THRESHOLD = 0.75
PAIR_THRESHOLD = 0.85
STABLE_SEMITONES = 2
MIN_RANGE_ATTEMPTS = 3
TIRED_LIMIT = 2
STABLE_WINDOW = 3  # estimates before and after each of the last 2 attempts
HINT_SD = 2.0

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


def _label(marg: dict[str, float]) -> ConfidenceLabel:
    if _top(marg)[1] >= TOP_THRESHOLD:
        return "high"
    if _best_pair(marg)[1] >= PAIR_THRESHOLD:
        return "medium"
    return "low"


def check(state: SessionState) -> DoneReason | None:
    """Why the session is done, or None to keep going."""
    marg = state.posterior.type_marginal()
    if _label(marg) != "low" and _stable(state.comfort_history) and state.range_attempts >= MIN_RANGE_ATTEMPTS:
        return "confident"
    if state.tired_count >= TIRED_LIMIT:
        return "tired"
    if state.attempts >= MAX_ATTEMPTS:
        return "max_attempts"
    return None


def _comfort_sd(state: SessionState) -> tuple[float, float]:
    probs = state.posterior.probs()
    mean_lo, mean_hi = state.posterior.comfort_estimate()
    var_lo = sum(p * (comfort(h)[0] - mean_lo) ** 2 for h, p in probs.items())
    var_hi = sum(p * (comfort(h)[1] - mean_hi) ** 2 for h, p in probs.items())
    return math.sqrt(var_lo), math.sqrt(var_hi)


def summary(state: SessionState) -> ResultSummary:
    """The result shown to the user (valid mid-session too, e.g. on Finish)."""
    post = state.posterior
    marg = post.type_marginal()
    voice_type, top_p = _top(marg)
    pair, pair_p = _best_pair(marg)
    label = _label(marg)
    between = None
    if label == "medium":
        between = pair[1] if pair[0] == voice_type else pair[0]

    sd_lo, sd_hi = _comfort_sd(state)
    hint: ImproveHint | None = None
    # SessionState carries the current warmth only; warmth never steps down in the app, so "not warm now"
    # stands in for "never warm".
    if sd_hi > HINT_SD and state.warmth != "warm":
        hint = "sing_higher_warm"
    elif sd_lo > HINT_SD:
        hint = "sing_lower"
    elif check(state) is None:
        hint = "more_songs"

    c_lo, c_hi = post.comfort_estimate()
    f_lo, f_hi = post.full_estimate()
    return ResultSummary(
        voice_type=voice_type,
        between=between,
        top_p=top_p,
        pair_p=pair_p,
        confidence_label=label,
        comfort=(round(c_lo), round(c_hi)),
        full=(round(f_lo), round(f_hi)),
        improve_hint=hint,
    )
