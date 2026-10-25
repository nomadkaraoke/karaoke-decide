"""Session comfort measured from the songs the singer rated comfortable (Task D1)."""

from __future__ import annotations

import pytest

from karaoke_decide.voice_session.comfort import (
    MEASURED_MIN_SONGS,
    MEASURED_MIN_VOICED_S,
    MIN_COMFORT_KEYS,
    comfort_band,
    counts_for_comfort,
    measured_comfort,
)
from karaoke_decide.voice_session.model import PitchSummary

from .builders import ctx, evidence, quiz, song


def _pitch(p10: float, p90: float, voiced_s: float = 60.0, *, held_low: float | None = None,
           held_high: float | None = None) -> PitchSummary:  # fmt: skip
    return PitchSummary(
        voiced_s=voiced_s,
        held_count=20,
        held_low=p10 - 1 if held_low is None else held_low,
        held_high=p90 + 1 if held_high is None else held_high,
        p10=p10,
        p50=(p10 + p90) / 2,
        p90=p90,
    )


def _comfy(p10: float, p90: float, voiced_s: float = 60.0, **quiz_over: object):  # type: ignore[no-untyped-def]
    q = quiz(**{"position": "comfortable", "difficulty": "easy", **quiz_over})
    return evidence(song(p10, p90), q, ctx(headphones=True), pitch=_pitch(p10, p90, voiced_s))


def test_constants() -> None:
    assert (MEASURED_MIN_SONGS, MEASURED_MIN_VOICED_S) == (2, 60.0)


def test_measured_comfort_weights_by_voiced_seconds() -> None:
    lo, hi = measured_comfort([_comfy(50, 58, 90), _comfy(54, 62, 30)])  # type: ignore[misc]
    assert lo == pytest.approx((50 * 90 + 54 * 30) / 120)
    assert hi == pytest.approx((58 * 90 + 62 * 30) / 120)


def test_measured_needs_two_comfortable_songs() -> None:
    assert measured_comfort([]) is None
    assert measured_comfort([_comfy(50, 58, 120)]) is None
    assert measured_comfort([_comfy(50, 58, 25), _comfy(51, 59, 25)]) is None  # 50 s voiced < 60
    assert measured_comfort([_comfy(50, 58, 30), _comfy(51, 59, 30)]) is not None


def test_measured_ignores_too_high_and_barely() -> None:
    base = [_comfy(50, 58), _comfy(51, 59)]
    expected = measured_comfort(base)
    assert expected is not None
    others = [
        _comfy(60, 70, 200, position="too_high"),
        _comfy(40, 48, 200, position="too_low"),
        _comfy(60, 70, 200, knew="barely"),
        evidence(song(60, 70), quiz(position="comfortable"), ctx(headphones=True), pitch=None),
        evidence(song(60, 70), quiz(position="comfortable"), ctx(headphones=True), pitch=_pitch(60, 70, 10)),
    ]
    assert measured_comfort([*base, *others]) == pytest.approx(expected)
    assert measured_comfort(others) is None


def test_measured_ignores_stretch_songs() -> None:
    """ "Comfortable but a stretch" reaches past the comfortable band (Andrew's falsetto song): not counted."""
    base = [_comfy(50, 58), _comfy(51, 59)]
    assert measured_comfort([*base, _comfy(56, 69, 200, difficulty="stretch")]) == pytest.approx(
        measured_comfort(base)  # type: ignore[arg-type]
    )
    assert measured_comfort([*base, _comfy(52, 60, 60, difficulty=None)]) != pytest.approx(measured_comfort(base))  # type: ignore[arg-type]


def test_measured_percentiles_stay_inside_held_chest_notes() -> None:
    """A p90 above the highest held chest note (a falsetto top relabelled out of held_high) is capped to it."""
    capped = evidence(
        song(56, 69), quiz(position="comfortable", difficulty="easy"), ctx(headphones=True),
        pitch=_pitch(56, 69, 60, held_low=52, held_high=66),
    )  # fmt: skip
    lo, hi = measured_comfort([_comfy(50, 58), capped])  # type: ignore[misc]
    assert hi == pytest.approx((58 + 66) / 2)
    assert lo == pytest.approx((50 + 56) / 2)


def test_measured_uses_the_pitch_as_sung() -> None:
    """A female-range song sung an octave down is measured where it was sung, not at the song's register."""
    down = evidence(song(62, 70), quiz(position="comfortable", difficulty="easy"), ctx(headphones=True),
                    pitch=_pitch(50, 58))  # fmt: skip
    assert measured_comfort([down, _comfy(51, 59)]) == pytest.approx((50.5, 58.5))


def test_band_is_at_least_11_keys_around_its_centre() -> None:
    """The mean of p10/p90 is narrower than the songs that made it; the saved band is at least MIN_COMFORT_KEYS
    keys (inclusive) around the measured centre, like the mic test's band (nomad-mobile #20)."""
    assert MIN_COMFORT_KEYS == 11
    assert comfort_band([_comfy(50, 58), _comfy(50, 58)], (45, 69)) == (49, 59)
    assert comfort_band([_comfy(52, 54), _comfy(52, 54)], (45, 69)) == (48, 58)
    wide = comfort_band([_comfy(48, 62), _comfy(48, 62)], (45, 69))
    assert wide == (48, 62)  # already wider: kept
    assert comfort_band([_comfy(50, 58)], (45, 69)) is None  # one song: not measured


def test_band_shifts_inside_the_full_range() -> None:
    """An overhang shifts the band (keeping its width) rather than cutting it."""
    assert comfort_band([_comfy(44, 50), _comfy(44, 50)], (45, 69)) == (45, 55)  # 42-52 shifted up 3
    assert comfort_band([_comfy(66, 70), _comfy(66, 70)], (45, 69)) == (59, 69)
    assert comfort_band([_comfy(40, 80), _comfy(40, 80)], (45, 69)) == (45, 69)  # wider than the range: the range


def test_each_counted_song_fits_inside_the_band() -> None:
    """Every song that measured the comfort stays singable against it (fit.is_singable needs p10..p90 inside the
    comfort band, tolerance 0): Andrew's song 4 was sung 48.9-58.9, below the mean band's 49."""
    song1 = _comfy(51.28, 57.24, 92.2)
    song4 = _comfy(48.87, 58.89, 66.0)
    lo, hi = comfort_band([song1, song4], (45, 69))  # type: ignore[misc]
    assert hi - lo >= MIN_COMFORT_KEYS - 1
    for ev in (song1, song4):
        assert counts_for_comfort(ev)
        assert ev.pitch is not None and lo <= ev.pitch.p10 and ev.pitch.p90 <= hi
    assert (lo, hi) == (48, 59)
