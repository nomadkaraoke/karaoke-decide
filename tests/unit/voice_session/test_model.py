"""Voice model: hypotheses, likelihoods and posterior updates (synthetic songs only)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from karaoke_decide.voice_session.model import (
    HYPOTHESES,
    VOICE_TYPES,
    PitchSummary,
    Posterior,
    SongDemand,
    comfort,
    full,
    likelihood_difficulty,
    likelihood_pitch,
    likelihood_position,
    likely_octave,
    sung_octave,
)
from karaoke_decide.voice_session.weights import OCTAVE_PRIOR, TEMP

from .builders import ctx, evidence, quiz, song


@dataclass
class Profile:
    method: str
    voice_type: str | None = None
    voice_type_between: str | None = None
    register: str | None = None


def _top_type(post: Posterior) -> str:
    marg = post.type_marginal()
    return max(marg, key=lambda t: marg[t])


def _assert_valid(post: Posterior) -> None:
    probs = post.probs()
    assert all(math.isfinite(v) for v in post.log_p.values())
    assert all(p > 0 for p in probs.values())
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_thirty_hypotheses_and_bounds() -> None:
    assert len(HYPOTHESES) == 30
    assert len(set(HYPOTHESES)) == 30
    assert list(VOICE_TYPES) == ["bass", "baritone", "tenor", "alto", "mezzo", "soprano"]
    assert {s for _, s in HYPOTHESES} == {-2, -1, 0, 1, 2}
    assert comfort(("baritone", 0)) == (50, 64)
    assert full(("baritone", 0)) == (45, 69)
    assert comfort(("soprano", -2)) == (63, 77)
    assert full(("bass", 2)) == (42, 66)


def test_uniform_is_valid() -> None:
    post = Posterior.uniform()
    _assert_valid(post)
    assert post.type_marginal()["tenor"] == pytest.approx(1 / 6)


def _voiced_in_octave(s_p50: float) -> PitchSummary:
    """Mic heard the singer in the song's own octave but caught no held notes (octave known, no range info)."""
    return PitchSummary(voiced_s=30, held_count=0, held_low=0, held_high=0, p10=0, p50=s_p50, p90=0)


def _quiz_only(post: Posterior, s_lo: float, s_hi: float, n: int = 5) -> Posterior:
    for _ in range(n):
        post = post.update(evidence(song(s_lo, s_hi), quiz(position="comfortable", difficulty="easy")))
    return post


MALE_TYPES = ("bass", "baritone", "tenor")


def test_comfortable_answers_concentrate_on_truth() -> None:
    """Quiz-only answers centred at 57: baritone leads the male types; its octave twin mezzo stays close.

    An honest near-tie between h and h+12 is intended without pitch; the profile prior or pitch separates them.
    """
    post = _quiz_only(Posterior.uniform(), 53, 61)
    _assert_valid(post)
    marg = post.type_marginal()
    assert max(MALE_TYPES, key=lambda t: marg[t]) == "baritone"
    assert marg["baritone"] > 0.3
    assert marg["baritone"] + marg["mezzo"] > 0.6


def test_quiz_only_female_songs_no_spurious_lean_for_baritone() -> None:
    """Baritone truth, comfortable+easy on female-range songs, uniform start: mezzo must not run away."""
    marg = _quiz_only(Posterior.uniform(), 64, 76).type_marginal()
    assert marg["mezzo"] < 2 * marg["baritone"]


def test_quiz_only_male_songs_no_spurious_lean_for_mezzo() -> None:
    """Mezzo truth, comfortable+easy on male-range songs, uniform start: baritone must not run away."""
    marg = _quiz_only(Posterior.uniform(), 50, 64).type_marginal()
    assert marg["baritone"] < 2 * marg["mezzo"]


def _octave_weights(h: tuple[str, int], s: SongDemand) -> dict[int, float]:
    c_lo, c_hi = comfort(h)
    raw = {}
    for o in (0, -12, 12):
        excess = max(c_lo - (s.p10 + o), s.p90 + o - c_hi)
        raw[o] = OCTAVE_PRIOR[o] / (1 + math.exp((excess - 1) / TEMP))
    z = sum(raw.values())
    return {o: v / z for o, v in raw.items()}


def test_unknown_octave_is_marginalised_with_fit_weighted_prior() -> None:
    s = song(64, 76)
    for h in (("baritone", 0), ("tenor", 1), ("soprano", -2), ("bass", 2)):
        w = _octave_weights(h, s)
        for a in ("too_low", "comfortable", "too_high", "both_ends"):
            mixed = sum(w[o] * likelihood_position(h, s, a, o) for o in w)
            assert likelihood_position(h, s, a, None) == pytest.approx(mixed)
        for d in ("easy", "stretch", "couldnt"):
            mixed = sum(w[o] * likelihood_difficulty(h, s, d, o) for o in w)
            assert likelihood_difficulty(h, s, d, None) == pytest.approx(mixed)
    assert sum(OCTAVE_PRIOR.values()) == pytest.approx(1.0)
    # A baritone mostly sings a female-range song down an octave.
    assert _octave_weights(("baritone", 0), s)[-12] > 0.5


def test_likely_octave_picks_with_penalty() -> None:
    assert likely_octave(("baritone", 0), song(64, 76)) == -12
    assert likely_octave(("baritone", 0), song(53, 61)) == 0
    assert likely_octave(("soprano", 0), song(53, 61)) == 12


def test_update_returns_new_object() -> None:
    post = Posterior.uniform()
    new = post.update(evidence(song(53, 61)))
    assert new is not post
    assert post.type_marginal()["tenor"] == pytest.approx(1 / 6)


def test_too_high_moves_mass_down() -> None:
    post = Posterior.uniform()
    before_lo, before_hi = post.comfort_estimate()
    after = post.update(evidence(song(60, 68), quiz(position="too_high"), pitch=_voiced_in_octave(64)))
    after_lo, after_hi = after.comfort_estimate()
    assert after_hi < before_hi
    assert after_lo < before_lo
    marg_before, marg_after = post.type_marginal(), after.type_marginal()
    assert marg_after["bass"] > marg_before["bass"]
    assert marg_after["soprano"] < marg_before["soprano"]


def test_too_low_moves_mass_up() -> None:
    post = Posterior.uniform()
    after = post.update(evidence(song(60, 68), quiz(position="too_low"), pitch=_voiced_in_octave(64)))
    assert after.comfort_estimate()[0] > post.comfort_estimate()[0]


def _mean_type_index(post: Posterior) -> float:
    marg = post.type_marginal()
    return sum(i * marg[t] for i, t in enumerate(VOICE_TYPES))


def test_too_high_without_pitch_on_mid_song_moves_mass_down() -> None:
    post = Posterior.uniform()
    after = post.update(evidence(song(56, 66), quiz(position="too_high")))
    assert _mean_type_index(after) < _mean_type_index(post)
    assert after.comfort_estimate()[1] < post.comfort_estimate()[1]


def test_too_high_without_pitch_on_low_song_moves_mass_down() -> None:
    post = Posterior.uniform()
    after = post.update(evidence(song(50, 58), quiz(position="too_high")))
    assert after.comfort_estimate()[1] < post.comfort_estimate()[1]


def test_position_likelihood_is_a_distribution_with_floor() -> None:
    s = song(60, 68)
    for h in HYPOTHESES:
        total = sum(likelihood_position(h, s, a, None) for a in ("too_low", "comfortable", "too_high", "both_ends"))
        assert total == pytest.approx(1.0)
        for a in ("too_low", "comfortable", "too_high", "both_ends"):
            assert likelihood_position(h, s, a, None) >= 0.15 / 4 - 1e-12


def test_difficulty_likelihood_is_a_distribution_with_floor() -> None:
    s = song(70, 82, spread=4)
    for h in HYPOTHESES:
        vals = [likelihood_difficulty(h, s, a, None) for a in ("easy", "stretch", "couldnt")]
        assert sum(vals) == pytest.approx(1.0)
        assert min(vals) >= 0.2 / 3 - 1e-12
    # A song far beyond a bass's full range is "couldnt"-likely for bass, "easy"-likely for soprano.
    assert likelihood_difficulty(("soprano", 0), s, "easy", 0) > likelihood_difficulty(("bass", 0), s, "easy", 0)
    assert likelihood_difficulty(("bass", 0), s, "couldnt", 0) > 0.5


def test_held_note_above_type_range_penalises_type() -> None:
    pitch = PitchSummary(voiced_s=40, held_count=5, held_low=60, held_high=74, p10=61, p50=67, p90=72)
    assert likelihood_pitch(("bass", 0), pitch) < 1e-3
    assert likelihood_pitch(("tenor", 0), pitch) < 0.5  # full top 72, held 74
    assert likelihood_pitch(("alto", 0), pitch) > 0.9  # full 53-77 covers 60-74
    post = Posterior.uniform().update(
        evidence(song(62, 72), quiz(position="comfortable"), ctx(headphones=True), pitch=pitch)
    )
    _assert_valid(post)
    assert post.type_marginal()["bass"] < 0.05


def test_unusable_pitch_is_ignored() -> None:
    short = PitchSummary(voiced_s=10, held_count=5, held_low=60, held_high=90, p10=61, p50=67, p90=72)
    few = PitchSummary(voiced_s=40, held_count=1, held_low=60, held_high=90, p10=55, p50=60, p90=63)
    s = song(55, 63)
    # Too little voicing: no octave, no range -> identical to quiz-only.
    base = Posterior.uniform().update(evidence(s))
    with_short = Posterior.uniform().update(evidence(s, pitch=short))
    for h in HYPOTHESES:
        assert with_short.log_p[h] == pytest.approx(base.log_p[h])
        assert likelihood_pitch(h, short) == 1.0
    # Enough voicing but < 2 held notes: octave is known, range evidence is not used.
    known = Posterior.uniform().update(evidence(s, pitch=_voiced_in_octave(59)))
    with_few = Posterior.uniform().update(evidence(s, pitch=few))
    for h in HYPOTHESES:
        assert with_few.log_p[h] == pytest.approx(known.log_p[h])
        assert likelihood_pitch(h, few) == 1.0


def test_sung_octave() -> None:
    s = song(64, 76)  # p50 70
    low = PitchSummary(voiced_s=30, held_count=3, held_low=52, held_high=63, p10=52, p50=58.5, p90=64)
    assert sung_octave(s, low) == -12
    same = PitchSummary(voiced_s=30, held_count=3, held_low=64, held_high=75, p10=64, p50=70.5, p90=76)
    assert sung_octave(s, same) == 0
    high = PitchSummary(voiced_s=30, held_count=3, held_low=64, held_high=75, p10=64, p50=81, p90=88)
    assert sung_octave(song(55, 65), high) == 12
    assert sung_octave(s, None) is None
    assert (
        sung_octave(s, PitchSummary(voiced_s=19.9, held_count=3, held_low=0, held_high=0, p10=0, p50=58, p90=0)) is None
    )


def test_octave_choice_for_female_song_by_male_singer() -> None:
    s = song(64, 76)
    # Sung an octave down, a baritone finds it comfortable.
    assert likelihood_position(("baritone", 0), s, "comfortable", -12) > 0.5
    # From a uniform start, one comfortable answer leaves baritone a real contender against mezzo.
    marg = Posterior.uniform().update(evidence(s, quiz(position="comfortable", difficulty="easy"))).type_marginal()
    assert marg["baritone"] >= 0.5 * marg["mezzo"]
    # From a baritone-leaning start, baritone stays top.
    prior = Posterior.from_profile(Profile(method="mic", voice_type="baritone"))
    after = prior.update(evidence(s, quiz(position="comfortable", difficulty="easy")))
    _assert_valid(after)
    assert _top_type(after) == "baritone"
    # With a pitch summary showing the lower octave, the same holds.
    pitch = PitchSummary(voiced_s=30, held_count=4, held_low=53, held_high=63, p10=52, p50=58, p90=64)
    after2 = prior.update(evidence(s, quiz(position="comfortable"), ctx(headphones=True), pitch=pitch))
    assert _top_type(after2) == "baritone"


def test_contradictory_answers_keep_valid_posterior() -> None:
    post = Posterior.uniform()
    s = song(58, 66)
    for i in range(10):
        pos = "too_high" if i % 2 == 0 else "too_low"
        post = post.update(evidence(s, quiz(position=pos, difficulty="couldnt")))
        _assert_valid(post)
    lo, hi = post.comfort_estimate()
    assert math.isfinite(lo) and math.isfinite(hi) and lo < hi


def test_extreme_evidence_never_zeroes_out() -> None:
    post = Posterior.uniform()
    pitch = PitchSummary(voiced_s=300, held_count=50, held_low=20, held_high=110, p10=20, p50=60, p90=110)
    for _ in range(50):
        post = post.update(evidence(song(20, 110, spread=5), quiz(position="both_ends", difficulty="easy"),
                                    ctx(headphones=True), pitch=pitch))  # fmt: skip
    _assert_valid(post)


def test_from_profile_mic_prior() -> None:
    marg = Posterior.from_profile(Profile(method="mic", voice_type="tenor")).type_marginal()
    total = 4 + 2 + 2 + 1 + 1 + 1
    assert marg["tenor"] == pytest.approx(4 / total)
    assert marg["baritone"] == pytest.approx(2 / total)
    assert marg["alto"] == pytest.approx(2 / total)
    assert marg["bass"] == pytest.approx(1 / total)
    # Shifts are uniform within a type.
    post = Posterior.from_profile(Profile(method="mic", voice_type="tenor"))
    probs = post.probs()
    assert probs[("tenor", -2)] == pytest.approx(probs[("tenor", 2)])
    # "Between" names the one adjacent type that gets the middle weight.
    marg_b = Posterior.from_profile(
        Profile(method="mic", voice_type="tenor", voice_type_between="alto")
    ).type_marginal()
    total_b = 4 + 2 + 1 + 1 + 1 + 1
    assert marg_b["alto"] == pytest.approx(2 / total_b)
    assert marg_b["baritone"] == pytest.approx(1 / total_b)


def test_from_profile_self_and_register_and_none() -> None:
    marg = Posterior.from_profile(Profile(method="self", voice_type="bass")).type_marginal()
    total = 3 + 1.5 + 1 * 4
    assert marg["bass"] == pytest.approx(3 / total)
    assert marg["baritone"] == pytest.approx(1.5 / total)
    reg = Posterior.from_profile(Profile(method="self", register="mid")).type_marginal()
    # Centres: baritone 57, tenor 60 lie in the mid band (57-62).
    total_r = 2 + 2 + 1 * 4
    assert reg["baritone"] == pytest.approx(2 / total_r)
    assert reg["tenor"] == pytest.approx(2 / total_r)
    assert reg["alto"] == pytest.approx(1 / total_r)
    high = Posterior.from_profile(Profile(method="self", register="high")).type_marginal()
    assert high["alto"] == high["mezzo"] == high["soprano"] > high["tenor"]
    for p in (None, Profile(method="other", voice_type="tenor"), Profile(method="self")):
        assert Posterior.from_profile(p).type_marginal()["tenor"] == pytest.approx(1 / 6)


def test_full_estimate_wider_than_comfort() -> None:
    post = Posterior.uniform()
    c_lo, c_hi = post.comfort_estimate()
    f_lo, f_hi = post.full_estimate()
    assert f_lo == pytest.approx(c_lo - 5) and f_hi == pytest.approx(c_hi + 5)
