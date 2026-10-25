"""Session comfort measured from the songs the singer rated comfortable (spec §10c, Task D1).

Pure Python. The model's comfort estimate is the posterior mean of each type's textbook band; once the singer
has sung enough songs they rated comfortable, what they actually sang is the better answer. ``measured_comfort``
is the voiced-seconds-weighted mean of p10 and p90 over songs that were:

* rated "comfortable" and not "a stretch" (a stretch reaches past the comfortable band: Andrew's falsetto song);
* backed by a usable pitch summary (``model.usable_pitch``);
* known better than "barely".

Excluded attempts never reach the evidence list. The percentiles are the singer's own notes, in the octave
they sang, so no octave shift applies; each is kept inside that song's held chest notes, so a falsetto top
relabelled out of ``held_high`` doesn't come back through p90.

``comfort_band`` turns that into the saved band. A mean of p10/p90 is narrower than the songs that produced it,
and song fit (``vocal_range.fit``, measured tier, tolerance 0) needs a song's p10..p90 inside the band, so:
at least MIN_COMFORT_KEYS keys (inclusive) around the measured centre, like the mic test's band
(nomad-mobile #20), widened further to cover every counted song's p10..p90, then shifted (not cut) to sit
inside the full range.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from karaoke_decide.voice_session.model import Evidence, usable_pitch

MEASURED_MIN_SONGS = 2
MEASURED_MIN_VOICED_S = 60.0
MIN_COMFORT_KEYS = 11  # inclusive: hi - lo >= 10 (nomad-mobile #20's mic band)


def counts_for_comfort(ev: Evidence) -> bool:
    """Whether this song's sung percentiles measure the comfortable range."""
    q = ev.quiz
    return (
        q.position == "comfortable" and q.difficulty in (None, "easy") and q.knew != "barely" and usable_pitch(ev.pitch)
    )


def _counted(evidence: Iterable[Evidence]) -> list[tuple[float, float, float]]:
    """(voiced_s, p10, p90) of each counted song, percentiles kept inside its held chest notes."""
    rows: list[tuple[float, float, float]] = []
    for ev in evidence:
        if not counts_for_comfort(ev):
            continue
        p = ev.pitch
        assert p is not None  # usable_pitch
        lo_bound, hi_bound = min(p.held_low, p.held_high), max(p.held_low, p.held_high)
        rows.append((p.voiced_s, min(max(p.p10, lo_bound), hi_bound), min(max(p.p90, lo_bound), hi_bound)))
    return rows


def _enough(rows: list[tuple[float, float, float]]) -> bool:
    return len(rows) >= MEASURED_MIN_SONGS and sum(w for w, _, _ in rows) >= MEASURED_MIN_VOICED_S


def measured_comfort(evidence: Iterable[Evidence]) -> tuple[float, float] | None:
    """(low, high) MIDI from the comfortable songs, or None below MEASURED_MIN_SONGS / MEASURED_MIN_VOICED_S."""
    rows = _counted(evidence)
    if not _enough(rows):
        return None
    total = sum(w for w, _, _ in rows)
    return sum(w * lo for w, lo, _ in rows) / total, sum(w * hi for w, _, hi in rows) / total


def _half_up(x: float) -> int:
    return math.floor(x + 0.5)


def comfort_band(evidence: Iterable[Evidence], full: tuple[int, int]) -> tuple[int, int] | None:
    """The saved comfortable band (MIDI ints) from the measured comfort, or None when it isn't measured.

    At least MIN_COMFORT_KEYS keys around the measured centre, covering every counted song's p10..p90, shifted
    inside ``full`` (the full-range estimate) without losing width; the full range itself when wider."""
    rows = _counted(evidence)
    if not _enough(rows):
        return None
    total = sum(w for w, _, _ in rows)
    lo = sum(w * a for w, a, _ in rows) / total
    hi = sum(w * b for w, _, b in rows) / total
    span = MIN_COMFORT_KEYS - 1
    if hi - lo < span:
        mid = (lo + hi) / 2
        lo, hi = mid - span / 2, mid + span / 2
    band_lo = min(_half_up(lo), math.floor(min(a for _, a, _ in rows)))
    band_hi = max(_half_up(hi), math.ceil(max(b for _, _, b in rows)))
    f_lo, f_hi = full
    if band_hi - band_lo >= f_hi - f_lo:
        return f_lo, f_hi
    if band_lo < f_lo:
        band_lo, band_hi = f_lo, band_hi + (f_lo - band_lo)
    if band_hi > f_hi:
        band_lo, band_hi = band_lo - (band_hi - f_hi), f_hi
    return band_lo, band_hi
