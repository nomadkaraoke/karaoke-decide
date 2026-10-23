"""Held-note detection -- same cases as the mobile ``findHeldNotes`` (3c plan, Task 1)."""

from __future__ import annotations

import numpy as np
import pytest

from karaoke_decide.voice_analysis.held import _rolling_median, held_notes
from karaoke_decide.voice_analysis.markers import VocalFrames


def frames_for(notes: list[dict], hop: float = 0.01) -> VocalFrames:
    """Mirror of the TS ``framesFor`` test signal: back-to-back notes, optional vibrato."""
    t: list[float] = []
    midi: list[float] = []
    start = 0.0
    for note in notes:
        n = round(note["dur"] / hop)
        cents = note.get("vibrato_cents", 0.0)
        rate = note.get("vibrato_hz", 0.0)
        for i in range(n):
            ti = start + i * hop
            t.append(ti)
            midi.append(note["midi"] + cents / 100 * np.sin(2 * np.pi * rate * i * hop))
        start += n * hop
    return VocalFrames(t=np.array(t), midi=np.array(midi), rms_db=np.zeros(len(t)))


def slide(from_midi: float, to_midi: float, semis_per_s: float, hop: float = 0.01) -> VocalFrames:
    n = round(abs(to_midi - from_midi) / semis_per_s / hop) + 1
    t = np.arange(n) * hop
    direction = 1 if to_midi >= from_midi else -1
    midi = from_midi + direction * semis_per_s * t
    return VocalFrames(t=t, midi=midi, rms_db=np.zeros(n))


def test_held_notes_match_ts_rules_held_a2():
    notes = held_notes(frames_for([{"midi": 45, "dur": 0.6}]))
    assert len(notes) == 1
    start, end, midi = notes[0]
    assert round(midi) == 45
    assert start == 0.0 and end == pytest.approx(0.59, abs=1e-6)


def test_held_notes_match_ts_rules_too_short():
    assert held_notes(frames_for([{"midi": 45, "dur": 0.4}])) == []


def test_held_notes_match_ts_rules_vibrato_counts():
    f = frames_for([{"midi": 57, "dur": 1.0, "vibrato_cents": 40, "vibrato_hz": 5.5}])
    notes = held_notes(f)
    assert len(notes) == 1
    assert round(notes[0][2]) == 57


def test_held_notes_match_ts_rules_slide_holds_nothing():
    assert held_notes(slide(45, 57, 4)) == []


def test_held_notes_match_ts_rules_octave_spike_neither_splits_nor_extends():
    f = frames_for([{"midi": 50, "dur": 1.0}])
    f.midi[[40, 41, 42]] = 62
    notes = held_notes(f)
    assert [round(m) for _, _, m in notes] == [50]
    assert notes[0][1] - notes[0][0] == pytest.approx(0.99, abs=1e-6)


def test_held_notes_match_ts_rules_150ms_gap_splits():
    a = frames_for([{"midi": 50, "dur": 0.6}])
    b = frames_for([{"midi": 50, "dur": 0.6}])
    t = np.concatenate([a.t, b.t + a.t[-1] + 0.15])
    f = VocalFrames(t=t, midi=np.concatenate([a.midi, b.midi]), rms_db=np.zeros(len(t)))
    assert len(held_notes(f)) == 2


def test_held_notes_unsorted_and_empty():
    assert held_notes(VocalFrames(t=np.empty(0), midi=np.empty(0), rms_db=np.empty(0))) == []
    f = frames_for([{"midi": 45, "dur": 0.6}])
    order = np.random.default_rng(0).permutation(len(f.t))
    shuffled = VocalFrames(t=f.t[order], midi=f.midi[order], rms_db=f.rms_db[order])
    assert held_notes(shuffled) == held_notes(f)


# Exact-boundary cases on 0.02 s frames: float subtraction lands just past each limit,
# and the TS eps (1e-9) keeps them inside. Times are rounded like real frame times.


def _frames_at(times: list[float], midi: float = 50.0) -> VocalFrames:
    t = np.array(times)
    return VocalFrames(t=t, midi=np.full(len(t), midi), rms_db=np.zeros(len(t)))


def test_gap_of_exactly_100ms_is_bridged():
    head = [round(i * 0.02, 2) for i in range(10)]  # 0.00 .. 0.18
    tail = [round(0.28 + i * 0.02, 2) for i in range(36)]  # 0.28 .. 0.98
    assert tail[0] - head[-1] > 0.1  # 0.10000000000000003 in floats
    notes = held_notes(_frames_at(head + tail))
    assert len(notes) == 1
    assert notes[0][0] == 0.0 and notes[0][1] == pytest.approx(0.98)


def test_hold_of_exactly_500ms_counts():
    times = [round(0.08 + i * 0.02, 2) for i in range(26)]  # 0.08 .. 0.58
    assert times[-1] - times[0] < 0.5  # 0.49999999999999994 in floats
    assert len(held_notes(_frames_at(times))) == 1


def test_frame_exactly_200ms_back_is_inside_the_median_window():
    t = np.array([round(0.36 + i * 0.02, 2) for i in range(11)])  # 0.36 .. 0.56
    assert t[-1] - t[0] > 0.2  # 0.20000000000000007 in floats
    midi = np.array([60.0, 50, 60, 50, 60, 50, 60, 50, 60, 50, 60])
    # Without the first frame the last window is five 50s and five 60s (median 55);
    # with it, six 60s win.
    assert _rolling_median(t, midi, 0.2)[-1] == 60.0
