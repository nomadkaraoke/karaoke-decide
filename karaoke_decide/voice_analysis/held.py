"""Held-note detection over voiced frames -- the same rules as the mobile ``findHeldNotes``.

Algorithm (3c plan, Task 1):

- sort by time; split into segments wherever consecutive frames are > ``max_gap_s`` apart;
- per segment, smooth with a trailing rolling median over ``median_window_s``
  (absorbs vibrato and short octave spikes);
- group consecutive smoothed values into runs while each stays within ``tol_cents`` of
  the run's median; a run lasting >= ``min_hold_s`` (last minus first frame time) is a
  held note ``(start, end, midi)`` with ``midi`` = the run's median.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from karaoke_decide.voice_analysis.markers import VocalFrames


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

    breaks = np.flatnonzero(np.diff(t) > max_gap_s) + 1
    bounds = [0, *breaks.tolist(), len(t)]
    tol = tol_cents / 100
    notes: list[tuple[float, float, float]] = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        seg_t, seg_m = t[lo:hi], midi[lo:hi]
        # Trailing window [t_i - w, t_i]: first index whose time is >= t_i - w.
        starts = np.searchsorted(seg_t, seg_t - median_window_s, side="left")
        smooth = np.array([np.median(seg_m[s : i + 1]) for i, s in enumerate(starts)])

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


def _close(
    notes: list[tuple[float, float, float]],
    t: np.ndarray,
    first: int,
    last: int,
    run_med: float,
    min_hold_s: float,
) -> None:
    if t[last] - t[first] >= min_hold_s:
        notes.append((float(t[first]), float(t[last]), run_med))
