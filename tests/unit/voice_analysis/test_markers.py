"""Per-song vocal markers -- synthetic frames; one librosa parity smoke test."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from karaoke_decide.vocal_range.ultrastar import parse_ultrastar
from karaoke_decide.voice_analysis.markers import (
    ANALYSIS_VERSION,
    FEMALE_COMFORT,
    FRAME_S,
    MALE_COMFORT,
    Section,
    SongMarkers,
    TrackAnalysis,
    VocalFrames,
    compute_markers,
    octave_split,
)

ULTRASTAR = Path(__file__).parents[2] / "fixtures" / "vocal_range" / "ultrastar"


def build(segments: list[tuple[float | None, float] | tuple[float | None, float, float]]) -> VocalFrames:
    """Frames at FRAME_S steps; ``(midi, dur[, rms_db])``, ``midi=None`` = silence."""
    t: list[float] = []
    midi: list[float] = []
    db: list[float] = []
    clock = 0.0
    for seg in segments:
        m, dur = seg[0], seg[1]
        level = seg[2] if len(seg) > 2 else -20.0
        n = round(dur / FRAME_S)
        if m is not None:
            t.extend(clock + i * FRAME_S for i in range(n))
            midi.extend([m] * n)
            db.extend([level] * n)
        clock += n * FRAME_S
    return VocalFrames(t=np.array(t), midi=np.array(midi, dtype=float), rms_db=np.array(db))


def ramp(lo: float, hi: float, n: int = 1001) -> VocalFrames:
    return VocalFrames(t=np.arange(n) * FRAME_S, midi=np.linspace(lo, hi, n), rms_db=np.full(n, -20.0))


def test_analysis_version():
    assert ANALYSIS_VERSION == 1


def test_range_percentiles():
    m = compute_markers(ramp(50, 70))
    assert isinstance(m, SongMarkers)
    assert (m.low, m.p10, m.p50, m.p90, m.high) == pytest.approx((51, 52, 60, 68, 69))
    assert m.voiced_s == pytest.approx(1001 * FRAME_S)


def test_empty_frames_raise():
    with pytest.raises(ValueError):
        compute_markers(VocalFrames(t=np.empty(0), midi=np.empty(0), rms_db=np.empty(0)))


def test_secs_above_thresholds():
    f = build([(60, 10), (65, 5), (70, 3), (74, 2), (77, 1), (44, 4), (50, 2)])
    m = compute_markers(f)
    assert set(m.secs_above) == {64, 67, 69, 72, 76}
    assert set(m.secs_below) == {45, 48, 52}
    assert m.secs_above[64] == pytest.approx(11)
    assert m.secs_above[67] == pytest.approx(6)
    assert m.secs_above[69] == pytest.approx(6)
    assert m.secs_above[72] == pytest.approx(3)
    assert m.secs_above[76] == pytest.approx(1)
    assert m.secs_below[45] == pytest.approx(4)
    assert m.secs_below[48] == pytest.approx(4)
    assert m.secs_below[52] == pytest.approx(6)


def test_octave_split_male_detects_between_octaves():
    assert MALE_COMFORT == (48, 64) and FEMALE_COMFORT == (55, 72)
    # Low octave (46-58) dips under 48; sung as written (58-70) climbs over 64.
    assert octave_split(58, 70, MALE_COMFORT) is True
    assert octave_split(50, 60, MALE_COMFORT) is False
    # Fits the female register as written.
    assert octave_split(58, 70, FEMALE_COMFORT) is False
    # Too wide for any octave is a range problem, not an octave split.
    assert octave_split(40, 72, MALE_COMFORT) is False
    # Only 1 semitone out at best: within tolerance.
    assert octave_split(59, 65, MALE_COMFORT) is False

    m = compute_markers(ramp(56.5, 71.5))  # p10 58, p90 70
    assert m.p10 == pytest.approx(58) and m.p90 == pytest.approx(70)
    assert m.octave_split_male is True
    assert m.octave_split_female is False


def test_section_jump_from_loud_sections():
    f = build([(55, 10), (63, 10), (57, 10), (65, 10), (80, 1)])
    # Boundaries sit 10 ms before each 10 s mark so no frame lands on one.
    loudness = [-10, -4, -9, -3, -2]  # the last is loudest but has only 1 s voiced: not eligible
    sections = [
        Section(start=max(0.0, 10 * i - 0.01), duration=10, loudness=db, key=0, key_confidence=0.9)
        for i, db in enumerate(loudness)
    ]
    m = compute_markers(f, sections=sections)
    assert m.section_jump == pytest.approx(64 - 56)
    assert compute_markers(f).section_jump is None
    # Fewer than three eligible sections: nothing to compare against.
    assert compute_markers(f, sections=sections[:2]).section_jump is None


def test_sustained_high_and_longest_held():
    segs: list = []
    for _ in range(10):
        segs += [(55, 1.0, -20.0), (None, 0.2)]
    segs += [(70, 0.8, -10.0), (None, 0.2), (70, 1.5, -10.0)]
    m = compute_markers(build(segs))
    assert m.p90 == pytest.approx(70)
    assert m.sustained_high_count == 2
    assert m.sustained_high_s == pytest.approx(0.78 + 1.48, abs=0.03)
    assert m.longest_held_s == pytest.approx(1.48, abs=0.03)
    assert m.high_loudness_db == pytest.approx(10.0)


def test_no_held_notes():
    f = VocalFrames(t=np.arange(200) * FRAME_S, midi=50 + 0.2 * np.arange(200), rms_db=np.zeros(200))
    m = compute_markers(f)
    assert (m.sustained_high_count, m.sustained_high_s, m.longest_held_s) == (0, 0.0, 0.0)


def test_density_prefers_ultrastar():
    song = parse_ultrastar((ULTRASTAR / "solo_utf8.txt").read_bytes())
    assert song is not None
    # Five 1 s voiced segments separated by 0.1 s gaps.
    segs: list = []
    for _ in range(5):
        segs += [(60, 1.0), (None, 0.1)]
    f = build(segs)
    # 7 ':' notes over 26 beats; one beat = 60 / (120 * 4) s = 0.125 s -> 3.25 s sung.
    with_us = compute_markers(f, ultrastar=song)
    assert with_us.density_source == "ultrastar"
    assert with_us.syllables_per_s == pytest.approx(7 / 3.25)
    onsets = compute_markers(f)
    assert onsets.density_source == "onsets"
    assert onsets.syllables_per_s == pytest.approx(5 / 5.0)


def test_longest_phrase_breaks_on_300ms_gap():
    broken = compute_markers(build([(60, 2), (None, 0.32), (60, 3)]))
    assert broken.longest_phrase_s == pytest.approx(3.0)
    joined = compute_markers(build([(60, 2), (None, 0.2), (60, 3)]))
    assert joined.longest_phrase_s == pytest.approx(5.2)


def test_intro_seconds():
    f = build([(None, 12), (60, 20)])
    assert compute_markers(f).intro_s == pytest.approx(12)
    sections = [
        Section(start=0, duration=8, loudness=-20, key=0, key_confidence=0.9),
        Section(start=8, duration=12, loudness=-6, key=0, key_confidence=0.9),
        Section(start=20, duration=6, loudness=-5, key=0, key_confidence=0.9),
        Section(start=26, duration=6, loudness=-7, key=0, key_confidence=0.9),
    ]
    # Median section loudness -6.5; the boundary at 8 s is the first within 6 dB.
    assert compute_markers(f, sections=sections).intro_s == pytest.approx(8)
    early = build([(None, 5), (60, 25)])
    assert compute_markers(early, sections=sections).intro_s == pytest.approx(5)


def test_key_changes_need_confidence():
    f = build([(60, 30)])
    keys = [(0, 0.9), (0, 0.9), (2, 0.8), (2, 0.3), (5, 0.7), (7, 0.6)]
    sections = [Section(start=5 * i, duration=5, loudness=-8, key=k, key_confidence=c) for i, (k, c) in enumerate(keys)]
    m = compute_markers(f, sections=sections, track=TrackAnalysis(key=9, mode=0, key_confidence=0.8))
    assert m.key_changes == 2  # 0->2 and 5->7; 2->5 has a low-confidence side
    assert (m.key, m.mode) == (9, 0)
    bare = compute_markers(f)
    assert (bare.key, bare.mode, bare.key_changes) == (None, None, 0)
    no_key = compute_markers(f, track=TrackAnalysis(key=-1, mode=1, key_confidence=0.0))
    assert (no_key.key, no_key.mode) == (None, None)


def test_vocal_frames_matches_analyze_vocals():
    pytest.importorskip("librosa")
    from karaoke_decide.vocal_range.pitch import analyze_vocals
    from karaoke_decide.voice_analysis.markers import vocal_frames

    sr = 16000

    def tone(midi: float, dur: float, amp: float = 0.3, harmonics: int = 5) -> np.ndarray:
        freq = 440 * 2 ** ((midi - 69) / 12)
        t = np.arange(int(dur * sr)) / sr
        y = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, harmonics + 1))
        env = np.minimum(1, np.minimum(t, dur - t) / 0.02)
        return (amp * y / harmonics * 2 * env).astype(np.float32)

    notes = list(range(50, 63))
    y = np.concatenate([tone(m, 30 / len(notes)) for m in notes])
    stats = analyze_vocals(y, sr, None)
    f = vocal_frames(y, sr)
    assert stats is not None
    assert len(f.t) == len(f.midi) == len(f.rms_db) > 0
    assert np.all(np.isfinite(f.rms_db))
    assert np.all(np.diff(f.t) > 0)
    m = compute_markers(f)
    assert m.p10 == pytest.approx(stats.p10, abs=0.01)
    assert m.p90 == pytest.approx(stats.p90, abs=0.01)
    assert m.voiced_s == pytest.approx(stats.voiced_s, abs=0.01)
