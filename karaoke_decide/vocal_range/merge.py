"""Merge the per-song vocal-range sources and derive per-artist ranges (pure, no I/O).

Interpretation notes (where the spec was ambiguous):

* Gemini confidence is carried in ``SourceRow.octave_confidence`` (the Gemini CSV
  ``confidence`` column); a Gemini row needs ``octave_confidence >= 0.3`` (None fails)
  AND ``gemini_kept(row)`` (the calibrated popularity band).
* Duplicates (several UltraStar files, several measured jobs) are resolved per
  ``lead_part``: keep the row whose p50 is closest to the group's median p50
  (ties -> the lower p50).
* Duet = more than one distinct ``lead_part`` other than ``"all"`` in the winning
  source; the part with the largest ``weight_s`` wins.
* ``n_sources`` = number of distinct sources that had at least one eligible row.
* Artist ``songs`` basis counts every merged song whose tier is not ``"rough"``;
  no merged tier is named "rough", so in practice all songs count.
* Artist keys: ``artist_of`` values, ``gemini_artists[].song_key``, ``mbid_of`` keys
  and ``gender_of`` keys. Gemini artist rows use ``song_key`` as the artist key.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from karaoke_decide.vocal_range.notes import VOICE_TYPES

Source = Literal["gen_measured", "nomad_measured", "ultrastar", "gemini"]

MIN_MEASURED_WEIGHT_S = 20.0
MIN_GEMINI_CONFIDENCE = 0.3
MIN_SONGS_FOR_ARTIST = 3

# (source, tier) in precedence order. Phase 3d: add ("pool_measured", "measured") here.
SOURCE_PRECEDENCE: tuple[tuple[str, str], ...] = (
    ("gen_measured", "measured"),
    ("nomad_measured", "measured"),
    ("ultrastar", "notes"),
    ("gemini", "estimated"),
)

# (low, high, p10, p50, p90)
GENDER_PRIOR: dict[str, tuple[int, int, int, int, int]] = {
    "male": (47, 67, 50, 57, 64),
    "female": (54, 74, 57, 64, 71),
}


@dataclass
class SourceRow:
    song_key: str
    source: Source
    source_id: str
    artist: str
    title: str
    low_midi: float
    high_midi: float
    p10: float
    p50: float
    p90: float
    octave_confidence: float | None
    lead_part: str
    weight_s: float
    pop_rank: int | None = None


@dataclass
class MergedSong:
    song_key: str
    low_midi: float
    high_midi: float
    p10: float
    p50: float
    p90: float
    tier: str
    source: str
    octave_confidence: float | None
    is_duet: bool
    n_sources: int


@dataclass
class ArtistRange:
    artist_key: str
    low_midi: float
    high_midi: float
    p10: float
    p50: float
    p90: float
    basis: Literal["songs", "gemini", "p412", "gender"]


def _eligible(source: str, r: SourceRow, gemini_kept: Callable[[SourceRow], bool]) -> bool:
    if source == "gemini":
        return r.octave_confidence is not None and r.octave_confidence >= MIN_GEMINI_CONFIDENCE and gemini_kept(r)
    if source in ("gen_measured", "nomad_measured", "pool_measured"):
        return r.weight_s >= MIN_MEASURED_WEIGHT_S
    return True


def _closest_to_median(rows: list[SourceRow]) -> SourceRow:
    med = statistics.median(r.p50 for r in rows)
    return min(rows, key=lambda r: (abs(r.p50 - med), r.p50))


def _pick(rows: list[SourceRow]) -> tuple[SourceRow, bool]:
    by_part: dict[str, list[SourceRow]] = defaultdict(list)
    for r in rows:
        by_part[r.lead_part].append(r)
    reps = {part: _closest_to_median(rs) for part, rs in by_part.items()}
    parts = [p for p in reps if p != "all"]
    if len(parts) > 1:
        return max((reps[p] for p in sorted(parts)), key=lambda r: r.weight_s), True
    return max(reps.values(), key=lambda r: r.weight_s), False


def merge_songs(rows: list[SourceRow], gemini_kept: Callable[[SourceRow], bool]) -> list[MergedSong]:
    """One MergedSong per song_key with an eligible row, sorted by song_key."""
    by_song: dict[str, dict[str, list[SourceRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if _eligible(r.source, r, gemini_kept):
            by_song[r.song_key][r.source].append(r)
    out: list[MergedSong] = []
    for key in sorted(by_song):
        by_source = by_song[key]
        for source, tier in SOURCE_PRECEDENCE:
            if source in by_source:
                best, duet = _pick(by_source[source])
                out.append(
                    MergedSong(
                        key,
                        best.low_midi,
                        best.high_midi,
                        best.p10,
                        best.p50,
                        best.p90,
                        tier,
                        source,
                        best.octave_confidence,
                        duet,
                        len(by_source),
                    )
                )
                break
    return out


def _med(vals: list[float]) -> float:
    return float(statistics.median(vals))


def artist_ranges(
    songs: list[MergedSong],
    artist_of: dict[str, str],
    gemini_artists: list[SourceRow],
    gemini_artist_kept: Callable[[SourceRow], bool],
    p412: dict[str, set[str]],
    mbid_of: dict[str, str],
    gender_of: dict[str, str],
) -> list[ArtistRange]:
    """Per-artist range by basis precedence: songs > gemini > p412 > gender; sorted by key."""
    by_artist: dict[str, list[MergedSong]] = defaultdict(list)
    for s in songs:
        a = artist_of.get(s.song_key)
        if a is not None and s.tier != "rough":
            by_artist[a].append(s)
    gem: dict[str, SourceRow] = {}
    for g in gemini_artists:
        ok = g.octave_confidence is not None and g.octave_confidence >= MIN_GEMINI_CONFIDENCE and gemini_artist_kept(g)
        cur = gem.get(g.song_key)
        if ok and (cur is None or (g.octave_confidence or 0) > (cur.octave_confidence or 0)):
            gem[g.song_key] = g
    keys = set(artist_of.values()) | {g.song_key for g in gemini_artists} | set(mbid_of) | set(gender_of)
    out: list[ArtistRange] = []
    for k in sorted(keys):
        ss = by_artist.get(k, [])
        if len(ss) >= MIN_SONGS_FOR_ARTIST:
            out.append(
                ArtistRange(
                    k,
                    _med([s.low_midi for s in ss]),
                    _med([s.high_midi for s in ss]),
                    _med([s.p10 for s in ss]),
                    _med([s.p50 for s in ss]),
                    _med([s.p90 for s in ss]),
                    "songs",
                )
            )
        elif k in gem:
            g = gem[k]
            out.append(ArtistRange(k, g.low_midi, g.high_midi, g.p10, g.p50, g.p90, "gemini"))
        elif types := p412.get(mbid_of.get(k, ""), set()) & set(VOICE_TYPES):
            vt = next(t for t in VOICE_TYPES if t in types)
            lo, hi = VOICE_TYPES[vt]
            out.append(ArtistRange(k, lo + 2, hi - 2, lo + 5, (lo + hi) / 2, hi - 5, "p412"))
        elif (prior := GENDER_PRIOR.get(gender_of.get(k, "").strip().lower())) is not None:
            out.append(ArtistRange(k, *prior, "gender"))
    return out
