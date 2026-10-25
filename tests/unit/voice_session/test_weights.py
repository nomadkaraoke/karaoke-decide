"""Context-weighted evidence: misses under bad conditions count less, successes never do."""

import pytest

from karaoke_decide.voice_session.weights import W_PITCH_HEADPHONES, W_PITCH_OPEN, evidence_weight

from .builders import ctx, evidence, quiz, song

S = song(55, 65)


def test_cold_miss_counts_half() -> None:
    ev = evidence(S, quiz(position="too_high", knew="well"), ctx(warmth="cold", posture="standing"))
    assert evidence_weight(ev, "quiz") == pytest.approx(0.5)


def test_cold_success_not_discounted() -> None:
    ev = evidence(S, quiz(position="comfortable", knew="well"), ctx(warmth="cold"))
    assert evidence_weight(ev, "quiz") == pytest.approx(1.0)


def test_couldnt_finish_is_a_miss_even_when_comfortable() -> None:
    ev = evidence(S, quiz(position="comfortable", difficulty="couldnt"), ctx(warmth="cold"))
    assert evidence_weight(ev, "quiz") == pytest.approx(0.5)


def test_barely_knew_discounts_everything() -> None:
    well_c = ctx(headphones=True)
    ev_well = evidence(S, quiz(knew="well"), well_c)
    ev_barely = evidence(S, quiz(knew="barely"), well_c)
    assert evidence_weight(ev_barely, "quiz") == pytest.approx(0.3)
    assert evidence_weight(ev_barely, "pitch") == pytest.approx(0.3 * evidence_weight(ev_well, "pitch"))
    # A miss on a barely-known song is discounted on top of the miss factors.
    ev_miss = evidence(S, quiz(knew="barely", position="too_low"), ctx(warmth="cold"))
    assert evidence_weight(ev_miss, "quiz") == pytest.approx(0.3 * 0.5)


def test_conditional_warmer_turns_no_into_demand() -> None:
    for cond in ("warmer", "practice"):
        ev = evidence(S, quiz(position="too_high", would_sing="no", would_work_if=frozenset({cond})))
        assert evidence_weight(ev, "quiz") == pytest.approx(0.3)
    ev_other = evidence(S, quiz(position="too_high", would_work_if=frozenset({"other_key"})))
    assert evidence_weight(ev_other, "quiz") == pytest.approx(1.0)


def test_tired_and_lying_compound() -> None:
    ev = evidence(S, quiz(position="too_high", voice_now="tired"), ctx(warmth="cold", posture="lying"))
    assert evidence_weight(ev, "quiz") == pytest.approx(0.5 * 0.5 * 0.7)


def test_stopped_early_miss_discounted() -> None:
    ev = evidence(S, quiz(position="both_ends"), stopped_early=True)
    assert evidence_weight(ev, "quiz") == pytest.approx(0.7)
    ev_ok = evidence(S, quiz(position="comfortable"), stopped_early=True)
    assert evidence_weight(ev_ok, "quiz") == pytest.approx(1.0)


def test_pitch_weight_headphones_vs_open() -> None:
    assert evidence_weight(evidence(S, c=ctx(headphones=True)), "pitch") == pytest.approx(W_PITCH_HEADPHONES)
    assert evidence_weight(evidence(S, c=ctx(headphones=False)), "pitch") == pytest.approx(W_PITCH_OPEN)
    assert (W_PITCH_HEADPHONES, W_PITCH_OPEN) == (2.0, 1.0)


def test_unknown_knew_uses_default() -> None:
    assert evidence_weight(evidence(S, quiz(knew=None)), "quiz") == pytest.approx(0.8)
