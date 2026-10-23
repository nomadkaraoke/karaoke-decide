"""Held-note detection over voiced frames -- the same rules as the mobile ``findHeldNotes``.

Algorithm (3c plan, Task 1):

- sort by time; split into segments wherever consecutive frames are > ``max_gap_s`` apart;
- per segment, smooth with a trailing rolling median over ``median_window_s``, inclusive
  of a frame exactly that far back (absorbs vibrato and short octave spikes);
- group consecutive smoothed values into runs while each stays within ``tol_cents`` of
  the run's median; a run lasting >= ``min_hold_s`` (last minus first frame time) is a
  held note ``(start, end, midi)`` with ``midi`` = the run's median.

All three time boundaries use the TS ``eps = 1e-9`` (gap ``<= max_gap_s + EPS`` joins,
window ``<= median_window_s + EPS`` is inside, hold ``>= min_hold_s - EPS`` counts), so
frame times on exact boundaries give the same notes on both platforms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from karaoke_decide.voice_analysis.markers import VocalFrames

EPS = 1e-9  # matches findHeldNotes' eps


def held_notes(
    f: VocalFrames,
    min_hold_s: float = 0.5,
    tol_cents: float = 50,
    max_gap_s: float = 0.1,
    median_window_s: float = 0.2,
) -> list[tuple[float, float, float]]:
    """Held notes as ``(start_s, end_s, midi)``, in time order."""
    if len(f.t) == 0:
        return []
    order = np.argsort(f.t, kind="stable")
    t = np.asarray(f.t, dtype=float)[order]
    midi = np.asarray(f.midi, dtype=float)[order]

    breaks = np.flatnonzero(np.diff(t) > max_gap_s + EPS) + 1
    bounds = [0, *breaks.tolist(), len(t)]
    tol = tol_cents / 100
    notes: list[tuple[float, float, float]] = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        seg_t, seg_m = t[lo:hi], midi[lo:hi]
        smooth = _rolling_median(seg_t, seg_m, median_window_s)

        run_start = 0
        run_med = float(smooth[0])
        for i in range(1, len(smooth)):
            if abs(smooth[i] - run_med) <= tol:
                run_med = float(np.median(smooth[run_start : i + 1]))
                continue
            _close(notes, seg_t, run_start, i - 1, run_med, min_hold_s)
            run_start, run_med = i, float(smooth[i])
        _close(notes, seg_t, run_start, len(smooth) - 1, run_med, min_hold_s)
    return notes


def _rolling_median(t: np.ndarray, midi: np.ndarray, window_s: float) -> np.ndarray:
    """Trailing median over frames with ``t[i] - t[lo] <= window_s + EPS`` (TS loop, verbatim)."""
    out = np.empty(len(t))
    lo = 0
    for i in range(len(t)):
        while t[i] - t[lo] > window_s + EPS:
            lo += 1
        out[i] = np.median(midi[lo : i + 1])
    return out


def _close(
    notes: list[tuple[float, float, float]],
    t: np.ndarray,
    first: int,
    last: int,
    run_med: float,
    min_hold_s: float,
) -> None:
    if t[last] - t[first] >= min_hold_s - EPS:
        notes.append((float(t[first]), float(t[last]), run_med))
