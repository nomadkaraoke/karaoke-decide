import pytest

from karaoke_decide.vocal_range.notes import hz_to_midi, midi_to_name, note_name_to_midi


def test_hz_to_midi():
    assert hz_to_midi(440) == pytest.approx(69)
    assert hz_to_midi(261.6256) == pytest.approx(60, abs=1e-3)


def test_note_names():
    assert note_name_to_midi("C#4") == 61
    assert note_name_to_midi("E♭3") == 51
    assert note_name_to_midi("Bb2") == 46
    assert note_name_to_midi("F♯4") == 66
    assert note_name_to_midi("C4") == 60
    assert note_name_to_midi("x") is None
    assert note_name_to_midi("H9") is None
    assert note_name_to_midi("") is None


def test_midi_to_name_rounds_and_uses_sharps():
    assert midi_to_name(45) == "A2"
    assert midi_to_name(46) == "A#2"
    assert midi_to_name(60.4) == "C4"
    assert midi_to_name(60.5) == "C#4"  # half rounds up, like JS Math.round
