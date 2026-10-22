"""Per-popularity-band calibration of Gemini estimates (pure, no I/O).

Interpretation notes:

* Song bands are always all reported (n may be 0). A band with n >= 30 is kept iff its
  median error <= 3.0. A band with n < 30 takes the *pooled decision*: the same test on
  all banded estimates pooled together (kept iff pooled n >= 30 and pooled median <= 3.0).
* Estimates with ``pop_rank`` outside every band (or None), or without truth, are ignored.
* Gemini artist estimates are classified to a voice type by nearest centre of
  ``(low_midi + high_midi) / 2`` to the voice-type centres; agreement = exact or adjacent
  in ``VOICE_TYPES`` order with ANY of the artist's P412 types. ``p412_types`` and
  ``measured_artist`` are keyed by the est row's ``song_key`` (the artist key).
* Artist ``BandStats.n`` = estimates with P412 or songs-basis evidence; ``median_err`` =
  median |Δp50| vs songs-basis artists (None if none). No pooling for artists.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from karaoke_decide.vocal_range.merge import ArtistRange, MergedSong, SourceRow
from karaoke_decide.vocal_range.notes import VOICE_TYPES

MIN_N = 30
MAX_MEDIAN_ERR = 3.0
MIN_P412_AGREEMENT = 0.7
MIN_TRUTH_CONFIDENCE = 0.5

SONG_BANDS: tuple[tuple[str, int, int], ...] = (("1-5k", 1, 5000), ("5k-20k", 5001, 20000), ("20k-50k", 20001, 50000))
ARTIST_BANDS: tuple[tuple[str, int, int], ...] = (("1-1k", 1, 1000), ("1k-5k", 1001, 5000), ("5k-20k", 5001, 20000))

_TYPE_ORDER = list(VOICE_TYPES)


@dataclass
class BandStats:
    band: str
    n: int
    median_err: float | None
    kept: bool


def _band_of(rank: int | None, bands: tuple[tuple[str, int, int], ...]) -> str | None:
    if rank is None:
        return None
    return next((name for name, lo, hi in bands if lo <= rank <= hi), None)


def _is_truth(s: MergedSong) -> bool:
    return s.tier == "measured" or (
        s.tier == "notes" and s.octave_confidence is not None and s.octave_confidence >= MIN_TRUTH_CONFIDENCE
    )


def calibrate_songs(est: list[SourceRow], truth: dict[str, MergedSong]) -> list[BandStats]:
    errs: dict[str, list[float]] = {name: [] for name, _, _ in SONG_BANDS}
    for r in est:
        band = _band_of(r.pop_rank, SONG_BANDS)
        t = truth.get(r.song_key)
        if band is None or t is None or not _is_truth(t):
            continue
        errs[band].append((abs(r.p10 - t.p10) + abs(r.p50 - t.p50) + abs(r.p90 - t.p90)) / 3)
    pooled = [e for v in errs.values() for e in v]
    pooled_ok = len(pooled) >= MIN_N and statistics.median(pooled) <= MAX_MEDIAN_ERR
    out = []
    for name, _, _ in SONG_BANDS:
        e = errs[name]
        med = float(statistics.median(e)) if e else None
        kept = (med is not None and med <= MAX_MEDIAN_ERR) if len(e) >= MIN_N else pooled_ok
        out.append(BandStats(name, len(e), med, kept))
    return out


def _voice_type(r: SourceRow) -> str:
    centre = (r.low_midi + r.high_midi) / 2
    return min(_TYPE_ORDER, key=lambda t: abs(sum(VOICE_TYPES[t]) / 2 - centre))


def _agrees(vt: str, types: set[str]) -> bool:
    i = _TYPE_ORDER.index(vt)
    return any(t in VOICE_TYPES and abs(_TYPE_ORDER.index(t) - i) <= 1 for t in types)


def calibrate_artists(
    est: list[SourceRow],
    p412_types: dict[str, set[str]],
    measured_artist: dict[str, ArtistRange],
) -> list[BandStats]:
    out = []
    for name, _, _ in ARTIST_BANDS:
        agree: list[bool] = []
        errs: list[float] = []
        evidence = 0
        for r in est:
            if _band_of(r.pop_rank, ARTIST_BANDS) != name:
                continue
            types = p412_types.get(r.song_key)
            m = measured_artist.get(r.song_key)
            has_songs = m is not None and m.basis == "songs"
            if types:
                agree.append(_agrees(_voice_type(r), types))
            if has_songs and m is not None:
                errs.append(abs(r.p50 - m.p50))
            if types or has_songs:
                evidence += 1
        checks: list[bool] = []
        if len(agree) >= MIN_N:
            checks.append(sum(agree) / len(agree) >= MIN_P412_AGREEMENT)
        if len(errs) >= MIN_N:
            checks.append(statistics.median(errs) <= MAX_MEDIAN_ERR)
        kept = bool(checks) and all(checks)
        out.append(BandStats(name, evidence, float(statistics.median(errs)) if errs else None, kept))
    return out
