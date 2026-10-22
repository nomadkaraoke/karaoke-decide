"""Stopping rule and result summary (spec §10a thresholds)."""

from __future__ import annotations

from karaoke_decide.voice_session import stopping
from karaoke_decide.voice_session.model import Posterior
from karaoke_decide.voice_session.select import SessionState

from .builders import posterior

STABLE = [(53.0, 67.0), (53.5, 67.2), (53.0, 67.0)]


def _state(post: Posterior, **overrides: object) -> SessionState:
    base: dict[str, object] = {
        "posterior": post,
        "warmth": "warm",
        "attempts": 3,
        "range_attempts": 3,
        "tired_count": 0,
        "done_keys": set(),
        "recent_strata": [],
        "comfort_history": list(STABLE),
    }
    base.update(overrides)
    return SessionState(**base)  # type: ignore[arg-type]


def test_constants() -> None:
    assert stopping.MAX_ATTEMPTS == 12
    assert stopping.TOP_THRESHOLD == 0.75
    assert stopping.PAIR_THRESHOLD == 0.85
    assert stopping.STABLE_SEMITONES == 2
    assert stopping.MIN_RANGE_ATTEMPTS == 3


def test_stop_confident_top_075_and_stable() -> None:
    state = _state(posterior({("tenor", 0): 0.8, ("baritone", 0): 0.2}))
    assert stopping.check(state) == "confident"
    s = stopping.summary(state)
    assert s.voice_type == "tenor"
    assert s.between is None
    assert s.confidence_label == "high"
    assert s.top_p > 0.79
    assert s.improve_hint is None
    assert s.comfort == (52, 66)  # 0.8·(53, 67) + 0.2·(50, 64) = (52.4, 66.4)
    assert s.full == (47, 71)


def test_stop_pair_085_reports_between() -> None:
    state = _state(posterior({("baritone", 0): 0.5, ("tenor", 0): 0.4, ("bass", 0): 0.1}))
    assert stopping.check(state) == "confident"
    s = stopping.summary(state)
    assert s.voice_type == "baritone"
    assert s.between == "tenor"
    assert s.confidence_label == "medium"
    assert abs(s.pair_p - 0.9) < 1e-6


def test_non_adjacent_pair_is_not_confident() -> None:
    state = _state(posterior({("baritone", 0): 0.5, ("mezzo", 0): 0.5}))
    assert stopping.check(state) is None
    assert stopping.summary(state).confidence_label == "low"


def test_not_stable_keeps_going() -> None:
    sure = posterior({("tenor", 0): 0.9, ("baritone", 0): 0.1})
    moved = [(50.0, 64.0), (53.0, 67.0), (53.0, 67.0)]
    assert stopping.check(_state(sure, comfort_history=moved)) is None
    # Movement before the last 2 attempts doesn't matter.
    assert stopping.check(_state(sure, comfort_history=[(40.0, 54.0), *STABLE])) == "confident"
    # Not enough history to judge stability yet.
    assert stopping.check(_state(sure, comfort_history=STABLE[-2:])) is None
    # Too few attempts with range evidence.
    assert stopping.check(_state(sure, range_attempts=2)) is None


def test_tired_twice_stops() -> None:
    unsure = Posterior.uniform()
    assert stopping.check(_state(unsure, tired_count=1)) is None
    assert stopping.check(_state(unsure, tired_count=2)) == "tired"


def test_max_attempts_12() -> None:
    unsure = Posterior.uniform()
    assert stopping.check(_state(unsure, attempts=11)) is None
    assert stopping.check(_state(unsure, attempts=12)) == "max_attempts"


def test_improve_hints() -> None:
    unsure = Posterior.uniform()
    cold = stopping.summary(_state(unsure, warmth="cold", attempts=1, range_attempts=1))
    assert cold.confidence_label == "low"
    assert cold.improve_hint == "sing_higher_warm"
    warm = stopping.summary(_state(unsure, warmth="warm", attempts=1, range_attempts=1))
    assert warm.improve_hint == "sing_lower"
    sure_not_done = stopping.summary(_state(posterior({("tenor", 0): 1.0}), range_attempts=1))
    assert sure_not_done.improve_hint == "more_songs"
    sure_done = stopping.summary(_state(posterior({("tenor", 0): 1.0})))
    assert sure_done.improve_hint is None
