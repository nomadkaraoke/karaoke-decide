from karaoke_decide.vocal_range.octave import (
    octave_error_rate,
    repair_octave,
    shifted,
)
from karaoke_decide.vocal_range.ultrastar import NoteStats


def stats(p10: float, p50: float, p90: float) -> NoteStats:
    return NoteStats(low=p10, high=p90, p10=p10, p50=p50, p90=p90, span=p90 - p10, sung_beats=100)


def test_male_song_written_octave_high_is_shifted_down():
    # male register 45-69 (+-2 -> 43..71, centre 57); p50 69 -> -12 lands on 57.
    # Only -12 is inside: [53, 61]; 0 -> 73 > 71, -24 -> 41 < 43.
    shift, conf = repair_octave(stats(65, 69, 73), "male")
    assert shift == -12
    assert conf == 0.9


def test_female_in_register_untouched():
    shift, conf = repair_octave(stats(57, 64, 72), "female")
    assert shift == 0
    assert conf == 0.9


def test_unknown_gender_ambiguous_conf():
    # register 45-76 (+-2 -> 43..78); narrow 62..66 fits at 0 and -12 -> ambiguous 0.5 * 0.6
    shift, conf = repair_octave(stats(62, 64, 66), None)
    assert shift == 0  # centre 60.5: |64-60.5|=3.5 beats |52-60.5|=8.5
    assert conf <= 0.54
    assert abs(conf - 0.3) < 1e-9


def test_two_octaves_off():
    shift, conf = repair_octave(stats(81, 81, 89), "male")
    assert shift == -24
    assert conf == 0.9


def test_no_candidate_inside_low_confidence():
    # span far wider than any register -> none inside -> 0.3
    shift, conf = repair_octave(stats(30, 57, 90), "male")
    assert shift == 0
    assert conf == 0.3


def test_tie_goes_to_zero():
    # male centre 57, p50 51 -> s=0 gives 6, s=12 gives 6: tie -> 0
    shift, _ = repair_octave(stats(51, 51, 55), "male")
    assert shift == 0


def test_shifted():
    out = shifted(stats(60, 64, 67), -12)
    assert (out.low, out.high, out.p10, out.p50, out.p90) == (48, 55, 48, 52, 55)
    assert out.span == 7 and out.sung_beats == 100


def test_error_rate_counts_near_octave_only():
    rate, n = octave_error_rate([(60, 62), (60, 48), (60, 68.9), (60, 51)])
    assert (rate, n) == (0.5, 4)


def test_error_rate_empty():
    assert octave_error_rate([]) == (0.0, 0)
