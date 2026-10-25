"""Simulated singers: the whole loop (choose → answer → update → stop) converges on the true voice type."""

from __future__ import annotations

import random

import pytest

from karaoke_decide.voice_session.model import HYPOTHESES, VOICE_TYPES, full, usable_pitch
from karaoke_decide.voice_session.simulate import (
    KS,
    mic_profile,
    run_population,
    sim_pitch,
    simulate,
    starting_posterior,
    synthetic_pool,
)

from .builders import song


def test_synthetic_pool_shape() -> None:
    pool = synthetic_pool(random.Random(1), n=300)
    assert len(pool) == 300
    assert len({c.song_key for c in pool}) == 300
    for c in pool:
        d = c.demand
        assert 45 <= d.p50 <= 76
        assert 8 <= d.p90 - d.p10 <= 20
        assert d.p50 == pytest.approx((d.p10 + d.p90) / 2)
        assert d.low == pytest.approx(d.p10 - 3)
        assert d.high == pytest.approx(d.p90 + 3)


def test_simulate_one_user_is_deterministic() -> None:
    pool = synthetic_pool(random.Random(2), n=300)
    a = simulate(("tenor", 0), pool, random.Random(3))
    b = simulate(("tenor", 0), pool, random.Random(3))
    assert a == b
    assert 1 <= a.attempts <= 12
    assert a.done_reason in ("confident", "tired", "max_attempts", "no_candidates")
    assert a.summary.voice_type in VOICE_TYPES


def test_simulate_runs_out_of_candidates() -> None:
    pool = synthetic_pool(random.Random(4), n=2)
    result = simulate(("baritone", 0), pool, random.Random(5), warmth_path=("warm",))
    assert result.done_reason == "no_candidates"  # confident needs 3 attempts with range evidence
    assert result.attempts == 2


def test_simulated_users_converge() -> None:
    """200 seeded users over every type × shift, starting from a self-reported register (3c) prior."""
    stats = run_population(random.Random(20261022), n_users=200, prior="self_register")
    assert stats.n == 200
    assert {h for h, _ in stats.results} == set(HYPOTHESES)
    assert stats.confident_within_10 >= 0.80, stats
    assert stats.adjacent >= 0.90, stats
    assert stats.exact >= 0.70, stats


def test_simulated_users_quiz_only_from_uniform_report() -> None:
    """Not gated: quiz answers alone from a flat start. Numbers are reported for tuning, not asserted."""
    stats = run_population(random.Random(20261022), n_users=200, prior="uniform")
    assert stats.n == 200
    for rate in (stats.confident_within_10, stats.adjacent, stats.exact):
        assert 0.0 <= rate <= 1.0
    print(f"\nquiz-only uniform prior: {stats.describe()}")


def test_mic_prior_sim_is_deterministic() -> None:
    pool = synthetic_pool(random.Random(6), n=300)
    a = simulate(("baritone", 1), pool, random.Random(7), prior="mic", pitch="headphones")
    b = simulate(("baritone", 1), pool, random.Random(7), prior="mic", pitch="headphones")
    assert a == b
    p1 = starting_posterior(("tenor", 0), "mic", random.Random(8))
    p2 = starting_posterior(("tenor", 0), "mic", random.Random(8))
    assert p1 == p2
    assert max(p1.type_marginal().values()) > 1 / 6  # a named type, not flat


def test_mic_profile_band_is_11_keys_and_biased_low() -> None:
    rng = random.Random(9)
    profiles = [mic_profile(("baritone", 0), rng) for _ in range(400)]
    assert all(p.comfort_high - p.comfort_low == 10 for p in profiles)
    centres = [(p.comfort_low + p.comfort_high) / 2 for p in profiles]
    true_centre = 57.0  # baritone comfort 50..64
    assert sum(centres) / len(centres) == pytest.approx(true_centre - 1.0, abs=0.4)
    assert all(p.method == "mic" and p.voice_type in VOICE_TYPES for p in profiles)
    assert sum(p.voice_type == "baritone" for p in profiles) > 100


def test_trace_has_one_summary_per_attempt() -> None:
    pool = synthetic_pool(random.Random(10), n=300)
    r = simulate(("alto", -1), pool, random.Random(11), prior="mic", pitch="open")
    assert len(r.trace) == r.attempts >= 1
    assert (r.trace[-1].voice_type, r.trace[-1].comfort) == (r.summary.voice_type, r.summary.comfort)
    short = simulate(("alto", -1), pool, random.Random(11), prior="uniform", budget=3)
    assert short.attempts <= 3 and len(short.trace) == short.attempts


def test_no_pitch_default_keeps_old_sessions() -> None:
    """pitch="none" and the old priors draw nothing extra: the existing gates see identical runs."""
    pool = synthetic_pool(random.Random(2), n=300)
    a = simulate(("tenor", 0), pool, random.Random(3))
    b = simulate(("tenor", 0), pool, random.Random(3), pitch="none", budget=None)
    assert a == b


def test_exact_at_k_uses_trace() -> None:
    stats = run_population(random.Random(12), n_users=30, prior="mic", pitch="headphones")
    types = list(VOICE_TYPES)
    assert set(stats.exact_at) == set(stats.adjacent_at) == set(stats.confident_within) == set(KS)
    for k in KS:
        at = [r.trace[min(k, len(r.trace)) - 1] for _, r in stats.results]
        dist = [abs(types.index(h[0]) - types.index(s.voice_type)) for (h, _), s in zip(stats.results, at, strict=True)]
        assert stats.exact_at[k] == pytest.approx(sum(d == 0 for d in dist) / 30)
        assert stats.adjacent_at[k] == pytest.approx(sum(d <= 1 for d in dist) / 30)
        conf = [r.done_reason == "confident" and r.attempts <= k for _, r in stats.results]
        assert stats.confident_within[k] == pytest.approx(sum(conf) / 30)
    assert stats.confident_within[10] == stats.confident_within_10
    assert "exact@5" in stats.describe()


def test_simulated_pitch_respects_singer_range() -> None:
    rng = random.Random(13)
    h = ("baritone", 0)
    f_lo, f_hi = full(h)
    assert sim_pitch(rng, h, song(50, 60), "none") is None
    leaks = 0
    for i in range(500):
        s = song(40 + i % 40, 52 + i % 40)
        p = sim_pitch(rng, h, s, "headphones")
        assert p is not None and usable_pitch(p)
        assert 60 <= p.voiced_s <= 180
        assert p.p10 <= p.p50 <= p.p90
        assert f_lo - 3 <= p.p10 and p.p90 <= f_hi + 3  # clipped into the chest range, then σ=0.7 noise
        assert p.held_low >= f_lo
        if p.held_high > f_hi:
            assert p.held_high == f_hi + 3  # a falsetto note leaked
            leaks += 1
    assert 0.2 <= leaks / 500 <= 0.4
