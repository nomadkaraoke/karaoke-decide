"""Read the published vocal-range tables (mb-refresh's ``vocal_range`` step) for the index builders.

The step writes ``song_vocal_range`` / ``artist_vocal_range`` to BigQuery and the same rows as
``gs://nomadkaraoke-musicbrainz-data/vocal-range/<run>/...tsv.gz`` (header row; NULL = empty cell;
bool = 1/0; a ``latest/`` copy is also kept), then ``vocal-range/latest.json`` =
``{run, sha256_song, sha256_artist, song_path, artist_path}`` LAST. Readers (song-index-build) fetch
the pointer, then read the per-run ``song_path`` / ``artist_path`` and check the sha256, so a
half-published run is never seen. catalog.db reads the BigQuery tables, offline.db the TSVs; both go through ``song_range`` / ``artist_range`` and ``VocalRanges.lookup`` so the
song -> artist ("rough") -> NULL fallback is the same everywhere.

``octave_confidence`` is the octave-repair confidence (UltraStar notes). The separate
``confidence`` column is Gemini's own confidence and is never read here.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Kept equal to karaoke_decide.etl.vocal_range_refresh by a test (that module pulls in BigQuery).
BUCKET = "nomadkaraoke-musicbrainz-data"
SONG_TSV_BLOB = "vocal-range/latest/song_vocal_range.tsv.gz"
ARTIST_TSV_BLOB = "vocal-range/latest/artist_vocal_range.tsv.gz"
POINTER_BLOB = "vocal-range/latest.json"

# offline.db stores the tier as an int (smaller on the phone).
TIER_CODES: dict[str, int] = {"measured": 4, "notes": 3, "estimated": 2, "rough": 1}
ROUGH = "rough"


@dataclass(frozen=True)
class SongRange:
    low: float
    high: float
    p10: float
    p50: float
    p90: float
    tier: str
    octave_confidence: float | None = None
    is_duet: bool | None = None  # None = unknown (artist fallback)


@dataclass(frozen=True)
class ArtistRange:
    low: float
    high: float
    p10: float
    p50: float
    p90: float
    basis: str


@dataclass(frozen=True)
class VocalRanges:
    songs: dict[str, SongRange] = field(default_factory=dict)
    artists: dict[str, ArtistRange] = field(default_factory=dict)
    run: str = ""

    def lookup(self, song_key: str, artist_key: str) -> SongRange | None:
        """The song's own range; else its artist's range as tier ``rough``; else None."""
        song = self.songs.get(song_key)
        if song is not None:
            return song
        artist = self.artists.get(artist_key)
        if artist is None:
            return None
        return SongRange(artist.low, artist.high, artist.p10, artist.p50, artist.p90, ROUGH)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true")
    return bool(value)


def _range(rec: Mapping[str, Any]) -> tuple[float, float, float, float, float] | None:
    nums = [_num(rec.get(c)) for c in ("low_midi", "high_midi", "p10", "p50", "p90")]
    if any(n is None for n in nums):
        return None
    lo, hi, p10, p50, p90 = (float(n) for n in nums if n is not None)
    return lo, hi, p10, p50, p90


def song_range(rec: Mapping[str, Any]) -> SongRange | None:
    """A ``song_vocal_range`` row (TSV strings or BigQuery values); None when unusable."""
    r = _range(rec)
    tier = rec.get("tier")
    if r is None or tier not in TIER_CODES:
        return None
    return SongRange(
        *r, tier=str(tier), octave_confidence=_num(rec.get("octave_confidence")), is_duet=_bool(rec.get("is_duet"))
    )


def artist_range(rec: Mapping[str, Any]) -> ArtistRange | None:
    r = _range(rec)
    if r is None:
        return None
    return ArtistRange(*r, basis=str(rec.get("basis") or ""))


def from_records(
    songs: Iterable[Mapping[str, Any]], artists: Iterable[Mapping[str, Any]], run: str = ""
) -> VocalRanges:
    song_map: dict[str, SongRange] = {}
    for rec in songs:
        key, s = rec.get("song_key"), song_range(rec)
        if key and s is not None:
            song_map[str(key)] = s
    artist_map: dict[str, ArtistRange] = {}
    for rec in artists:
        key, a = rec.get("artist_key"), artist_range(rec)
        if key and a is not None:
            artist_map[str(key)] = a
    return VocalRanges(song_map, artist_map, run)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""), delimiter="\t"))


def load(song_tsv: Path, artist_tsv: Path, run: str = "") -> VocalRanges:
    """Both TSVs (plain or gzipped) -> ``VocalRanges``."""
    return from_records(_read_tsv(song_tsv), _read_tsv(artist_tsv), run)
