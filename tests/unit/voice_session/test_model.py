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
    centre,
    comfort,
    full,
    key_shift_direction,
    likelihood_difficulty,
    likelihood_pitch,
    likelihood_position,
    likelihood_tessitura,
    likely_octave,
    sung_octave,
    tessitura_applies,
)
from karaoke_decide.voice_session.weights import (
    HELD_HIGH_FALSETTO_P,
    MIC_BIAS,
    OCTAVE_PRIOR,
    PRIOR_FLOOR,
    PRIOR_SD,
    TEMP,
    W_PITCH_HEADPHONES,
    W_TESSITURA,
)

from .builders import ctx, evidence, quiz, song


@dataclass
class Profile:
    method: str
    voice_type: str | None = None
    voice_type_between: str | None = None
    register: str | None = None
    confidence: str | None = None
    comfort_low: int | None = None
    comfort_high: int | None = None


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
    # A held top far above the range keeps HELD_HIGH_FALSETTO_P: it may be a falsetto note the app missed.
    assert likelihood_pitch(("bass", 0), pitch) == pytest.approx(HELD_HIGH_FALSETTO_P, abs=1e-3)
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


@pytest.mark.parametrize(("confidence", "named"), [("high", 4), ("medium", 4), ("low", 3), (None, 3)])
def test_from_profile_session_weights_follow_confidence(confidence: str | None, named: float) -> None:
    """A saved session profile seeds the next session: mic weights when confident, self weights when low."""
    marg = Posterior.from_profile(Profile(method="session", voice_type="tenor", confidence=confidence)).type_marginal()
    assert marg["tenor"] / marg["bass"] == pytest.approx(named)


def test_key_shift_direction_at_likely_octave() -> None:
    post = Posterior({h: 0.0 if h == ("baritone", 0) else -50.0 for h in HYPOTHESES})  # comfort 50-64
    assert key_shift_direction(post, SongDemand(low=50, high=66, p10=52, p50=58, p90=64)) == "down"
    assert key_shift_direction(post, SongDemand(low=44, high=60, p10=46, p50=52, p90=58)) == "up"
    # a higher-voice song the baritone takes down an octave, where it sits low: "up", not "down"
    assert key_shift_direction(post, SongDemand(low=60, high=71, p10=62, p50=66, p90=69)) == "up"


def test_full_estimate_wider_than_comfort() -> None:
    post = Posterior.uniform()
    c_lo, c_hi = post.comfort_estimate()
    f_lo, f_hi = post.full_estimate()
    assert f_lo == pytest.approx(c_lo - 5) and f_hi == pytest.approx(c_hi + 5)


# ---------------------------------------------------------------- tessitura (E2)


def _tess_by_type(pitch: PitchSummary, position: str = "comfortable", octave: int | None = 0) -> dict[str, float]:
    """Mean tessitura likelihood over each type's shifts."""
    out: dict[str, float] = {}
    for t in VOICE_TYPES:
        vals = [likelihood_tessitura(h, pitch, position, octave) for h in HYPOTHESES if h[0] == t]  # type: ignore[arg-type]
        out[t] = sum(vals) / len(vals)
    return out


def _sung(p10: float, p50: float, p90: float, *, voiced_s: float = 90.0) -> PitchSummary:
    return PitchSummary(
        voiced_s=voiced_s, held_count=20, held_low=p10 - 1, held_high=p90 + 1, p10=p10, p50=p50, p90=p90
    )


def test_tessitura_separates_bass_baritone_tenor() -> None:
    """A comfortable song sung at 49/54/59: tenor (comfort 53-67) is at least 3x less likely than bass or baritone."""
    by_type = _tess_by_type(_sung(49, 54, 59))
    assert by_type["baritone"] >= 3 * by_type["tenor"]
    assert by_type["bass"] >= 3 * by_type["tenor"]
    assert by_type["tenor"] >= 3 * by_type["alto"]
    # Andrew's sung median (~56.5) sits at baritone's comfort centre (57): baritone over bass and tenor.
    andrew = _tess_by_type(_sung(50, 56.5, 58))
    assert andrew["baritone"] > andrew["bass"]
    assert andrew["baritone"] > andrew["tenor"]
    # The posterior follows: one comfortable song with this pitch from a flat start.
    s = song(49, 59)
    post = Posterior.uniform().update(
        evidence(s, quiz(position="comfortable"), ctx(headphones=True), _sung(49, 54, 59))
    )
    marg = post.type_marginal()
    assert marg["baritone"] >= 3 * marg["tenor"]


def test_tessitura_neutral_when_not_comfortable() -> None:
    pitch = _sung(49, 54, 59)
    for position in ("too_high", "too_low", "both_ends"):
        assert all(likelihood_tessitura(h, pitch, position, 0) == 1.0 for h in HYPOTHESES)  # type: ignore[arg-type]


def test_tessitura_neutral_without_usable_pitch() -> None:
    thin = _sung(49, 54, 59, voiced_s=10)
    few = PitchSummary(voiced_s=90, held_count=1, held_low=49, held_high=59, p10=49, p50=54, p90=59)
    for h in HYPOTHESES:
        assert likelihood_tessitura(h, None, "comfortable", 0) == 1.0
        assert likelihood_tessitura(h, thin, "comfortable", None) == 1.0
        assert likelihood_tessitura(h, few, "comfortable", 0) == 1.0


def test_tessitura_uses_the_pitch_as_sung_in_any_octave() -> None:
    """The percentiles are the singer's own notes: a female-range song sung an octave down still reads as baritone."""
    pitch = _sung(51, 57, 62)
    assert _tess_by_type(pitch, octave=-12) == _tess_by_type(pitch, octave=0)
    by_type = _tess_by_type(pitch, octave=-12)
    assert max(by_type, key=lambda t: by_type[t]) == "baritone"


def test_tessitura_weight_counts_in_the_update() -> None:
    s = song(49, 59)
    pitch = _sung(49, 54, 59)
    with_pitch = Posterior.uniform().update(evidence(s, quiz(position="comfortable"), ctx(headphones=True), pitch))
    for h in HYPOTHESES:
        o = sung_octave(s, pitch)
        expected = (
            math.log(likelihood_position(h, s, "comfortable", o))
            + W_PITCH_HEADPHONES * math.log(likelihood_pitch(h, pitch))
            + W_TESSITURA * math.log(likelihood_tessitura(h, pitch, "comfortable", o))
        )
        ref = ("baritone", 0)
        ref_expected = (
            math.log(likelihood_position(ref, s, "comfortable", o))
            + W_PITCH_HEADPHONES * math.log(likelihood_pitch(ref, pitch))
            + W_TESSITURA * math.log(likelihood_tessitura(ref, pitch, "comfortable", o))
        )
        assert with_pitch.log_p[h] - with_pitch.log_p[ref] == pytest.approx(expected - ref_expected, abs=1e-9)


def test_posterior_stays_valid_with_extreme_pitch() -> None:
    post = Posterior.uniform()
    for p in (_sung(10, 20, 30), _sung(100, 110, 127), _sung(20, 64, 110)):
        for _ in range(20):
            post = post.update(evidence(song(55, 65), quiz(position="comfortable"), ctx(headphones=True), p))
            _assert_valid(post)


# ---------------------------------------------------------------- range-shaped prior (E3)


def test_mic_prior_peaks_at_shifted_centre() -> None:
    """A mic reading reads low: the prior peaks MIC_BIAS above the measured comfort centre."""
    assert MIC_BIAS["mic"] == 1.0 and MIC_BIAS["session"] == 0.0
    mic = Posterior.from_profile(Profile(method="mic", voice_type="bass", comfort_low=50, comfort_high=60)).probs()
    peak = max(mic.values())
    assert {h for h, p in mic.items() if p == pytest.approx(peak)} == {h for h in HYPOTHESES if centre(h) == 56}
    session = Posterior.from_profile(
        Profile(method="session", voice_type="baritone", confidence="medium", comfort_low=50, comfort_high=60)
    ).probs()
    s_peak = max(session.values())
    assert {h for h, p in session.items() if p == pytest.approx(s_peak)} == {h for h in HYPOTHESES if centre(h) == 55}
    # The weight falls off as a Gaussian of PRIOR_SD over the hypothesis centres, on top of the floor.
    h_far = ("tenor", 2)  # centre 62: 6 above 56
    w = PRIOR_FLOOR + (1 - PRIOR_FLOOR) * math.exp(-(6**2) / (2 * PRIOR_SD**2))
    assert mic[h_far] / peak == pytest.approx(w)


def test_mic_prior_ignores_the_named_type_when_comfort_is_known() -> None:
    """A low mic reading names bass for a baritone; the range-shaped prior doesn't hand bass 4x."""
    marg = Posterior.from_profile(
        Profile(method="mic", voice_type="bass", comfort_low=49, comfort_high=59)
    ).type_marginal()
    assert marg["baritone"] > marg["bass"]


def test_mic_prior_never_below_floor() -> None:
    for c_lo in (30, 45, 55, 70, 90):
        probs = Posterior.from_profile(
            Profile(method="mic", voice_type="tenor", comfort_low=c_lo, comfort_high=c_lo + 10)
        ).probs()
        assert min(probs.values()) / max(probs.values()) >= PRIOR_FLOOR - 1e-12


def test_wrong_mic_type_recoverable_in_3_songs() -> None:
    """A baritone whose mic test read bass (comfort 46-56) is baritone after 3 comfortable songs sung with pitch."""
    post = Posterior.from_profile(Profile(method="mic", voice_type="bass", comfort_low=46, comfort_high=56))
    for p10, p90 in ((51, 61), (53, 63), (50, 62)):
        p50 = (p10 + p90) / 2
        pitch = PitchSummary(voiced_s=90, held_count=20, held_low=p10 - 1, held_high=p90 + 1, p10=p10, p50=p50, p90=p90)
        post = post.update(evidence(song(p10, p90), quiz(position="comfortable", difficulty="easy"),
                                    ctx(headphones=True), pitch=pitch))  # fmt: skip
    _assert_valid(post)
    assert _top_type(post) == "baritone"


def test_self_prior_unchanged() -> None:
    """Self reports (even with a comfort band) and comfort-less mic profiles keep the type weights."""
    with_band = Posterior.from_profile(Profile(method="self", voice_type="bass", comfort_low=50, comfort_high=60))
    without = Posterior.from_profile(Profile(method="self", voice_type="bass"))
    assert with_band.log_p == pytest.approx(without.log_p)
    marg = Posterior.from_profile(Profile(method="mic", voice_type="tenor")).type_marginal()
    assert marg["tenor"] / marg["bass"] == pytest.approx(4.0)


def test_held_high_above_range_is_robust_to_a_missed_falsetto_note() -> None:
    """One falsetto note in held_high can't rule a type out: the top bound keeps a floor, the bottom doesn't."""
    assert HELD_HIGH_FALSETTO_P == 0.3
    leak = PitchSummary(voiced_s=90, held_count=20, held_low=49, held_high=72, p10=50, p50=55, p90=59)
    assert likelihood_pitch(("baritone", 0), leak) >= HELD_HIGH_FALSETTO_P
    assert likelihood_pitch(("baritone", 0), leak) < likelihood_pitch(("tenor", 0), leak)
    low = PitchSummary(voiced_s=90, held_count=20, held_low=40, held_high=55, p10=42, p50=48, p90=53)
    assert likelihood_pitch(("tenor", 0), low) < 0.01  # full bottom 48: held 40 is a real chest note


def test_tessitura_only_from_comfortable_easy_songs() -> None:
    """A "comfortable but a stretch" song reached past the comfortable band (Andrew's falsetto song), so its sung
    percentiles don't count as tessitura, the same rule as the measured comfort."""
    s = song(56, 69)
    pitch = _sung(56, 60.5, 69)
    base = Posterior.uniform()
    stretch = base.update(evidence(s, quiz(position="comfortable", difficulty="stretch"), ctx(headphones=True), pitch))
    o = sung_octave(s, pitch)
    expected = {
        h: math.log(likelihood_position(h, s, "comfortable", o))
        + math.log(likelihood_difficulty(h, s, "stretch", o))
        + W_PITCH_HEADPHONES * math.log(likelihood_pitch(h, pitch))
        for h in HYPOTHESES
    }
    ref = ("baritone", 0)
    for h in HYPOTHESES:
        assert stretch.log_p[h] - stretch.log_p[ref] == pytest.approx(expected[h] - expected[ref], abs=1e-9)
    for difficulty in ("easy", None):
        q = quiz(position="comfortable", difficulty=difficulty)
        assert tessitura_applies(evidence(s, q, ctx(headphones=True), pitch))
    assert not tessitura_applies(evidence(s, quiz(position="comfortable", difficulty="stretch"), pitch=pitch))
    assert not tessitura_applies(evidence(s, quiz(position="too_high", difficulty="easy"), pitch=pitch))
