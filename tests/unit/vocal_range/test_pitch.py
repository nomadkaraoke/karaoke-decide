"""pyin vocal analysis tests -- synthetic tones only."""

import numpy as np
import pytest

pytest.importorskip("librosa")

from karaoke_decide.vocal_range.pitch import PitchStats, analyze_vocals  # noqa: E402

SR = 16000


def _tone(midi: float, dur: float, amp: float = 0.3, harmonics: int = 5) -> np.ndarray:
    f = 440 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(dur * SR)) / SR
    y = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, harmonics + 1))
    y = y / harmonics * 2
    env = np.minimum(1, np.minimum(t, dur - t) / 0.02)  # 20 ms fades
    return (amp * y * env).astype(np.float32)


def test_sine_scale_stats():
    # 50 -> 62 in 13 steps of ~2.3 s each (30 s total)
    notes = list(range(50, 63))
    seg = 30 / len(notes)
    y = np.concatenate([_tone(m, seg) for m in notes])
    stats = analyze_vocals(y, SR)
    assert isinstance(stats, PitchStats)
    assert stats.voiced_s >= 25
    assert stats.low == pytest.approx(50, abs=0.5)
    assert stats.high == pytest.approx(62, abs=0.5)
    assert stats.p10 == pytest.approx(51, abs=0.3)
    assert stats.p90 == pytest.approx(61, abs=0.3)
    assert stats.p50 == pytest.approx(56, abs=0.3)


def test_word_windows_exclude_outside_frames():
    inside = np.concatenate([_tone(60, 12), _tone(67, 12)])  # 0-24 s
    outside = _tone(44.0, 6, amp=0.8)  # ~104 Hz, loud, 24-30 s (<~90 Hz fails pyin prob>=0.5)
    y = np.concatenate([inside, outside])
    stats = analyze_vocals(y, SR, windows=[(0.0, 24.0)])
    assert stats is not None
    assert stats.low > 58
    # control: without windows the loud low tone is counted
    ctrl = analyze_vocals(y, SR)
    assert ctrl is not None and ctrl.low < 50


def test_energy_gating_ignores_bleed():
    voice = np.concatenate([_tone(57, 12), _tone(64, 12), _tone(60, 6)])  # -6 dB-ish
    bleed = _tone(83.0, 30, amp=0.003, harmonics=1)  # ~1 kHz, ~-40 dB
    stats = analyze_vocals(voice + bleed, SR)
    assert stats is not None
    assert stats.high < 66


def test_short_returns_none():
    y = _tone(60, 10)
    assert analyze_vocals(y, SR) is None
