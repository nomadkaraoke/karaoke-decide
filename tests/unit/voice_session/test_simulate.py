"""Simulated singers: the whole loop (choose → answer → update → stop) converges on the true voice type."""

from __future__ import annotations

import random

import pytest

from karaoke_decide.voice_session.model import HYPOTHESES, VOICE_TYPES
from karaoke_decide.voice_session.simulate import run_population, simulate, synthetic_pool


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
