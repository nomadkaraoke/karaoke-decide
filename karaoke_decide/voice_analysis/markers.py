"""Per-song vocal markers from a separated vocals stem (Phase 3d voice session).

Frames come from 3c's pyin pipeline (``vocal_range.pitch``), so range numbers agree with
``analyze_vocals``. Everything after ``vocal_frames`` is pure numpy over the frames plus
optional Spotify sections/track analysis and a matched UltraStar file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from karaoke_decide.vocal_range.pitch import HOP_LENGTH, TARGET_SR, voiced_midi_frames
from karaoke_decide.vocal_range.ultrastar import SINGABLE_KINDS, UltraStarSong
from karaoke_decide.voice_analysis.held import held_notes

ANALYSIS_VERSION = 1
FRAME_S = HOP_LENGTH / TARGET_SR  # seconds of audio each kept frame stands for

SECS_ABOVE_MIDI = (64, 67, 69, 72, 76)  # E4 G4 A4 C5 E5
SECS_BELOW_MIDI = (45, 48, 52)  # A2 C3 E3
MALE_COMFORT = (48, 64)
FEMALE_COMFORT = (55, 72)
OCTAVE_SPLIT_MIN_COST = 2.0  # semitones outside the register at the best octave
SECTION_MIN_VOICED_S = 3.0
SUSTAINED_HIGH_BELOW_P90 = 1.0
PHRASE_MAX_GAP_S = 0.3
INTRO_LOUDNESS_WITHIN_DB = 6.0
KEY_MIN_CONFIDENCE = 0.5
_ONSET_GAP_S = 1.5 * FRAME_S  # a missing frame starts a new voiced segment


@dataclass
class VocalFrames:
    """Voiced, gated pyin frames: time (s), MIDI pitch and RMS loudness (dB)."""

    t: np.ndarray
    midi: np.ndarray
    rms_db: np.ndarray


@dataclass
class Section:
    """One row of BigQuery ``spotify_audio_analysis_sections``."""

    start: float
    duration: float
    loudness: float
    key: int
    key_confidence: float


@dataclass
class TrackAnalysis:
    """Key fields of BigQuery ``spotify_audio_analysis_tracks``."""

    key: int
    mode: int
    key_confidence: float


@dataclass
class SongMarkers:
    low: float  # P5
    high: float  # P95
    p10: float
    p50: float
    p90: float
    voiced_s: float
    secs_above: dict[int, float]  # seconds sung at or above each MIDI note
    secs_below: dict[int, float]  # seconds sung at or below each MIDI note
    octave_split_male: bool
    octave_split_female: bool
    section_jump: float | None
    sustained_high_count: int
    sustained_high_s: float
    longest_held_s: float
    high_loudness_db: float | None
    syllables_per_s: float | None
    density_source: Literal["ultrastar", "onsets"]
    longest_phrase_s: float
    intro_s: float
    key: int | None
    mode: int | None
    key_changes: int


def vocal_frames(y: np.ndarray, sr: int) -> VocalFrames:
    """pyin frames of a vocals stem with 3c's settings and energy gating."""
    t, midi, rms_db = voiced_midi_frames(y, sr)
    return VocalFrames(t=t, midi=midi, rms_db=rms_db)


def _outside(lo: float, hi: float, register: tuple[int, int]) -> float:
    return max(0.0, register[0] - lo) + max(0.0, hi - register[1])


def octave_split(p10: float, p90: float, register: tuple[int, int]) -> bool:
    """True when no octave fits: one octave down is too low and as written is too high.

    ``cost(o)`` = semitones of ``[p10+o, p90+o]`` outside the register, for o in
    -12/0/+12. Split when the best cost is >= 2 and some adjacent pair of octaves has
    the lower one only too low and the higher one only too high (a range too wide for
    any octave is a range problem, not a split).
    """
    reg_lo, reg_hi = register
    if min(_outside(p10 + o, p90 + o, register) for o in (-12, 0, 12)) < OCTAVE_SPLIT_MIN_COST:
        return False

    def too_low(o: int) -> bool:
        return p10 + o < reg_lo and p90 + o <= reg_hi

    def too_high(o: int) -> bool:
        return p90 + o > reg_hi and p10 + o >= reg_lo

    return any(too_low(o) and too_high(o + 12) for o in (-12, 0))


def _section_jump(f: VocalFrames, sections: list[Section] | None) -> float | None:
    """Median MIDI of the two loudest eligible sections minus that of the other eligible ones.

    Eligible = >= 3 s voiced; needs at least three eligible sections.
    """
    if not sections:
        return None
    eligible: list[tuple[float, np.ndarray]] = []
    for s in sections:
        mask = (f.t >= s.start) & (f.t < s.start + s.duration)
        if mask.sum() * FRAME_S >= SECTION_MIN_VOICED_S:
            eligible.append((s.loudness, f.midi[mask]))
    if len(eligible) < 3:
        return None
    eligible.sort(key=lambda e: e[0], reverse=True)
    loud = np.concatenate([m for _, m in eligible[:2]])
    rest = np.concatenate([m for _, m in eligible[2:]])
    return float(np.median(loud) - np.median(rest))


def _high_loudness_db(f: VocalFrames, p90: float) -> float | None:
    """Mean dB of frames >= P90 minus mean dB of frames within P25-P75."""
    p25, p75 = np.percentile(f.midi, [25, 75])
    high = f.rms_db[f.midi >= p90]
    mid = f.rms_db[(f.midi >= p25) & (f.midi <= p75)]
    if high.size == 0 or mid.size == 0:
        return None
    return float(high.mean() - mid.mean())


def _ultrastar_density(song: UltraStarSong) -> float | None:
    """Sung ``:``/``*`` notes per sung second (all parts; one beat = 60 / (BPM*4) s)."""
    notes = [n for part in song.parts.values() for n in part if n.kind in SINGABLE_KINDS]
    sung_s = sum(n.length for n in notes) * 60.0 / (song.bpm * 4)
    return len(notes) / sung_s if sung_s > 0 else None


def _runs(t: np.ndarray, max_gap_s: float) -> list[tuple[float, float]]:
    """(first, last) frame times of runs whose silent gaps (between frames) are < ``max_gap_s``."""
    gaps = np.diff(t) - FRAME_S
    breaks = np.flatnonzero(gaps >= max_gap_s) + 1
    bounds = [0, *breaks.tolist(), len(t)]
    return [(float(t[lo]), float(t[hi - 1])) for lo, hi in zip(bounds[:-1], bounds[1:])]


def _onset_density(t: np.ndarray, voiced_s: float) -> float | None:
    """Voiced-segment onsets (a new segment after any missing frame) per voiced second."""
    onsets = 1 + int(np.count_nonzero(np.diff(t) > _ONSET_GAP_S))
    return onsets / voiced_s if voiced_s > 0 else None


def _intro_s(first_voiced: float, sections: list[Section] | None) -> float:
    """First voiced frame, capped by the first section boundary (start > 0) whose
    loudness is within 6 dB of the median section loudness."""
    if not sections:
        return first_voiced
    median = float(np.median([s.loudness for s in sections]))
    for s in sorted(sections, key=lambda s: s.start):
        if s.start > 0 and abs(s.loudness - median) <= INTRO_LOUDNESS_WITHIN_DB:
            return min(first_voiced, float(s.start))
    return first_voiced


def _key_changes(sections: list[Section] | None) -> int:
    if not sections:
        return 0
    ordered = sorted(sections, key=lambda s: s.start)
    return sum(
        1
        for a, b in zip(ordered, ordered[1:])
        if a.key != b.key and min(a.key_confidence, b.key_confidence) >= KEY_MIN_CONFIDENCE
    )


def compute_markers(
    f: VocalFrames,
    *,
    sections: list[Section] | None = None,
    track: TrackAnalysis | None = None,
    ultrastar: UltraStarSong | None = None,
) -> SongMarkers:
    """All per-song markers; raises ValueError when there are no voiced frames."""
    if len(f.t) == 0:
        raise ValueError("no voiced frames")
    order = np.argsort(f.t, kind="stable")
    f = VocalFrames(t=np.asarray(f.t)[order], midi=np.asarray(f.midi)[order], rms_db=np.asarray(f.rms_db)[order])

    low, p10, p50, p90, high = (float(v) for v in np.percentile(f.midi, [5, 10, 50, 90, 95]))
    voiced_s = len(f.t) * FRAME_S

    held = held_notes(f)
    sustained = [(s, e) for s, e, m in held if m >= p90 - SUSTAINED_HIGH_BELOW_P90]

    if ultrastar is not None:
        syllables_per_s = _ultrastar_density(ultrastar)
        density_source: Literal["ultrastar", "onsets"] = "ultrastar"
    else:
        syllables_per_s = _onset_density(f.t, voiced_s)
        density_source = "onsets"

    # Spotify key -1 = no key detected; the mode is meaningless then too.
    key = track.key if track is not None and track.key >= 0 else None
    return SongMarkers(
        low=low,
        high=high,
        p10=p10,
        p50=p50,
        p90=p90,
        voiced_s=voiced_s,
        secs_above={m: float(np.count_nonzero(f.midi >= m)) * FRAME_S for m in SECS_ABOVE_MIDI},
        secs_below={m: float(np.count_nonzero(f.midi <= m)) * FRAME_S for m in SECS_BELOW_MIDI},
        octave_split_male=octave_split(p10, p90, MALE_COMFORT),
        octave_split_female=octave_split(p10, p90, FEMALE_COMFORT),
        section_jump=_section_jump(f, sections),
        sustained_high_count=len(sustained),
        sustained_high_s=float(sum(e - s for s, e in sustained)),
        longest_held_s=float(max((e - s for s, e, _ in held), default=0.0)),
        high_loudness_db=_high_loudness_db(f, p90),
        syllables_per_s=syllables_per_s,
        density_source=density_source,
        longest_phrase_s=max(last - first + FRAME_S for first, last in _runs(f.t, PHRASE_MAX_GAP_S)),
        intro_s=_intro_s(float(f.t[0]), sections),
        key=key,
        mode=track.mode if track is not None and key is not None else None,
        key_changes=_key_changes(sections),
    )
