"""pyin vocal-pitch analysis of an isolated vocal stem.

Standalone on purpose: depends only on numpy (librosa is imported lazily), with no
package-relative imports, so ``scripts/vocal_range/analyze_nomad_stems.py`` can run
against a plain copy of this file on a box without a decide checkout.

Phase 3d will lift ``_voiced_midi_frames`` into a public frame-level ``vocal_frames``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TARGET_SR = 16000
FMIN = 65.0
FMAX = 1100.0
FRAME_LENGTH = 1024
HOP_LENGTH = 320  # 20 ms at 16 kHz
MIN_VOICED_PROB = 0.5
ENERGY_GATE_DB = 25.0  # keep frames within this many dB of the P95 frame loudness
MIN_VOICED_S = 20.0


@dataclass
class PitchStats:
    low: float  # P5 of voiced MIDI
    high: float  # P95 of voiced MIDI
    p10: float
    p50: float
    p90: float
    voiced_s: float


def _voiced_midi_frames(
    y: np.ndarray,
    sr: int,
    windows: list[tuple[float, float]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (times_s, midi) of kept voiced frames.

    Resamples to 16 kHz, runs pyin, keeps voiced frames with prob >= 0.5, then either
    keeps only frames inside ``windows`` (word timings) or, without windows, applies
    energy gating (RMS dB >= P95 RMS dB - 25) to drop stem bleed.
    """
    import librosa

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=0) if y.shape[0] < y.shape[-1] else y.mean(axis=-1)
    if sr != TARGET_SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=TARGET_SR)
    empty = (np.empty(0), np.empty(0))
    if y.size < FRAME_LENGTH:
        return empty

    f0, voiced_flag, voiced_prob = librosa.pyin(
        y,
        fmin=FMIN,
        fmax=FMAX,
        sr=TARGET_SR,
        frame_length=FRAME_LENGTH,
        hop_length=HOP_LENGTH,
    )
    n = len(f0)
    t = librosa.frames_to_time(np.arange(n), sr=TARGET_SR, hop_length=HOP_LENGTH)
    keep = voiced_flag & (voiced_prob >= MIN_VOICED_PROB) & np.isfinite(f0)

    if windows is not None:
        in_win = np.zeros(n, dtype=bool)
        for start, end in windows:
            in_win |= (t >= start) & (t <= end)
        keep &= in_win
    else:
        rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH)[0][:n]
        db = 20 * np.log10(rms + 1e-10)
        keep[: len(db)] &= db >= (np.percentile(db, 95) - ENERGY_GATE_DB)
        keep[len(db) :] = False

    midi = 69 + 12 * np.log2(f0[keep] / 440.0)
    return t[keep], midi


def analyze_vocals(
    y: np.ndarray,
    sr: int,
    windows: list[tuple[float, float]] | None = None,
) -> PitchStats | None:
    """Pitch statistics of a vocal stem, or None when under 20 s of voiced audio."""
    t, midi = _voiced_midi_frames(y, sr, windows)
    voiced_s = len(t) * HOP_LENGTH / TARGET_SR
    if voiced_s < MIN_VOICED_S:
        return None
    p5, p10, p50, p90, p95 = (float(v) for v in np.percentile(midi, [5, 10, 50, 90, 95]))
    return PitchStats(low=p5, high=p95, p10=p10, p50=p50, p90=p90, voiced_s=voiced_s)
