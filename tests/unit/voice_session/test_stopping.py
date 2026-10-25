"""Stopping rule and result summary (spec §10a thresholds)."""

from __future__ import annotations

from karaoke_decide.voice_session import stopping
from karaoke_decide.voice_session.model import PitchSummary, Posterior
from karaoke_decide.voice_session.select import SessionState

from .builders import ctx, evidence, posterior, quiz, song

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
    # After MEDIUM_LEAN_UNTIL songs a settled adjacent pair is enough (early on it also needs a lean: see below).
    state = _state(
        posterior({("baritone", 0): 0.5, ("tenor", 0): 0.4, ("bass", 0): 0.1}),
        attempts=stopping.MEDIUM_LEAN_UNTIL + 1,
    )
    assert stopping.check(state) == "confident"
    s = stopping.summary(state)
    assert s.voice_type == "baritone"
    assert s.between == "tenor"
    assert s.confidence_label == "medium"
    assert abs(s.pair_p - 0.9) < 1e-6


def test_early_medium_needs_a_clear_lean() -> None:
    """Within MEDIUM_LEAN_UNTIL songs, "between X and Y" stops only when X leads with MEDIUM_MIN_TOP: a near 50/50
    pair is where a type is most often wrong (adjacent types share ranges at the ends of their shifts)."""
    assert (stopping.MEDIUM_MIN_TOP, stopping.MEDIUM_LEAN_UNTIL) == (0.7, 5)
    split = posterior({("baritone", 0): 0.5, ("tenor", 0): 0.4, ("bass", 0): 0.1})
    for attempts in (3, 4, 5):
        assert stopping.check(_state(split, attempts=attempts)) is None
        assert stopping.summary(_state(split, attempts=attempts)).confidence_label == "low"
    lean = posterior({("baritone", 0): 0.72, ("tenor", 0): 0.2, ("bass", 0): 0.08})
    assert stopping.check(_state(lean, attempts=3)) == "confident"
    s = stopping.summary(_state(lean, attempts=3))
    assert (s.confidence_label, s.between) == ("medium", "tenor")


def test_measured_comfort_settles_the_range() -> None:
    """A comfort measured from comfortable songs is what the result shows, so the model's moving estimate
    (comfort_history) no longer holds the label at "low"."""
    sure = posterior({("baritone", 0): 0.9, ("tenor", 0): 0.1})
    moved = [(48.0, 62.0), (51.0, 65.0), (50.7, 64.7)]
    assert stopping.check(_state(sure, comfort_history=moved)) is None
    measured = [_comfortable_sung(50, 58), _comfortable_sung(50.5, 58.5)]
    assert stopping.check(_state(sure, comfort_history=moved, evidence=measured)) == "confident"
    assert stopping.summary(_state(sure, comfort_history=moved, evidence=measured)).confidence_label == "high"
    # Still needs MIN_RANGE_ATTEMPTS attempts with range evidence.
    assert stopping.check(_state(sure, comfort_history=moved, evidence=measured, range_attempts=2)) is None


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
    assert sure_not_done.confidence_label == "low"  # too few range attempts to claim confidence
    sure_done = stopping.summary(_state(posterior({("tenor", 0): 1.0})))
    assert sure_done.improve_hint is None
    assert sure_done.confidence_label == "high"


SURE = posterior({("tenor", 0): 0.95, ("baritone", 0): 0.05})


def test_finish_after_two_attempts_is_low() -> None:
    s = stopping.summary(_state(SURE, attempts=2, range_attempts=2))
    assert s.voice_type == "tenor"
    assert s.top_p > 0.9
    assert s.confidence_label == "low"
    assert s.between is None


def test_unstable_range_is_low() -> None:
    moved = [(50.0, 64.0), (53.0, 67.0), (53.0, 67.0)]
    assert stopping.summary(_state(SURE, comfort_history=moved)).confidence_label == "low"


def test_no_candidates_end() -> None:
    # Ran out of songs after 2 attempts: low confidence, and "more songs" is not a hint we can act on.
    state = _state(SURE, attempts=2, range_attempts=2)
    assert stopping.summary(state).improve_hint == "more_songs"
    ended = stopping.summary(state, done_reason="no_candidates")
    assert ended.confidence_label == "low"
    assert ended.improve_hint is None
    # Range hints still apply when they do.
    cold = stopping.summary(_state(Posterior.uniform(), warmth="cold", attempts=2), done_reason="no_candidates")
    assert cold.improve_hint == "sing_higher_warm"
    warm = stopping.summary(_state(Posterior.uniform(), attempts=2), done_reason="no_candidates")
    assert warm.improve_hint == "sing_lower"


def _comfortable_sung(p10: float, p90: float, voiced_s: float = 90.0):  # type: ignore[no-untyped-def]
    pitch = PitchSummary(voiced_s=voiced_s, held_count=20, held_low=p10 - 1, held_high=p90 + 1, p10=p10,
                         p50=(p10 + p90) / 2, p90=p90)  # fmt: skip
    return evidence(song(p10, p90), quiz(position="comfortable", difficulty="easy"), ctx(headphones=True), pitch=pitch)


def test_summary_uses_measured_and_clamps_to_full() -> None:
    bari = posterior({("baritone", 0): 1.0})  # comfort 50-64, full 45-69
    model = stopping.summary(_state(bari))
    assert (model.comfort, model.comfort_source) == ((50, 64), "model")
    measured = stopping.summary(_state(bari, evidence=[_comfortable_sung(50.4, 57.6), _comfortable_sung(50.2, 58.2)]))
    assert measured.comfort_source == "measured"
    assert measured.comfort == (50, 58)
    # Clamped inside the full-range estimate.
    low = stopping.summary(_state(bari, evidence=[_comfortable_sung(40, 50), _comfortable_sung(41, 51)]))
    assert low.comfort == (45, 50)
    high = stopping.summary(_state(bari, evidence=[_comfortable_sung(66, 74), _comfortable_sung(66, 75)]))
    assert high.comfort == (66, 69)
    # An explicit evidence list overrides the state's.
    assert stopping.summary(_state(bari), evidence=[_comfortable_sung(50, 58), _comfortable_sung(50, 58)]).comfort == (
        50,
        58,
    )
