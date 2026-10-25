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
"""

from __future__ import annotations

from collections.abc import Iterable

from karaoke_decide.voice_session.model import Evidence, usable_pitch

MEASURED_MIN_SONGS = 2
MEASURED_MIN_VOICED_S = 60.0


def counts_for_comfort(ev: Evidence) -> bool:
    """Whether this song's sung percentiles measure the comfortable range."""
    q = ev.quiz
    return (
        q.position == "comfortable" and q.difficulty in (None, "easy") and q.knew != "barely" and usable_pitch(ev.pitch)
    )


def measured_comfort(evidence: Iterable[Evidence]) -> tuple[float, float] | None:
    """(low, high) MIDI from the comfortable songs, or None below MEASURED_MIN_SONGS / MEASURED_MIN_VOICED_S."""
    rows: list[tuple[float, float, float]] = []
    for ev in evidence:
        if not counts_for_comfort(ev):
            continue
        p = ev.pitch
        assert p is not None  # usable_pitch
        lo_bound, hi_bound = min(p.held_low, p.held_high), max(p.held_low, p.held_high)
        p10 = min(max(p.p10, lo_bound), hi_bound)
        p90 = min(max(p.p90, lo_bound), hi_bound)
        rows.append((p.voiced_s, p10, p90))
    total = sum(w for w, _, _ in rows)
    if len(rows) < MEASURED_MIN_SONGS or total < MEASURED_MIN_VOICED_S:
        return None
    return sum(w * lo for w, lo, _ in rows) / total, sum(w * hi for w, _, hi in rows) / total
