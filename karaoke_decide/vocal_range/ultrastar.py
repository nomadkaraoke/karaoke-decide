"""UltraStar ``.txt`` parser and duration-weighted note statistics."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MIDI_OFFSET = 60  # UltraStar pitch 0 = C4 = MIDI 60
SINGABLE_KINDS = {":", "*"}  # F (freestyle), R (rap), G (rap-golden) are not pitched
_VIDEO_ID_RE = re.compile(r"(?:^|,)a=([^,]+)")
_YEAR_RE = re.compile(r"^[0-9]{4}$")
_PART_RE = re.compile(r"^P\s*([12])\s*$")


@dataclass
class UltraStarNote:
    beat: int
    length: int
    pitch: int
    kind: str


@dataclass
class UltraStarSong:
    artist: str
    title: str
    language: str | None
    year: int | None
    video_id: str | None
    bpm: float
    gap_ms: float
    relative: bool
    parts: dict[str, list[UltraStarNote]]  # "P1"/"P2" for duets, "all" for solos


@dataclass
class NoteStats:
    low: float  # weighted P5 (MIDI)
    high: float  # weighted P95
    p10: float
    p50: float
    p90: float
    span: float  # high - low
    sung_beats: int  # total note length in beats
    raw_min: float  # unweighted min MIDI of the kept notes
    raw_max: float  # unweighted max MIDI of the kept notes


def _decode(raw: bytes) -> str:
    """Honour ``#ENCODING:``; else strip the BOM and try UTF-8; else CP1252."""
    sniff = raw.decode("latin-1")
    match = re.search(r"^\s*#ENCODING:\s*(\S+)", sniff.lstrip("﻿\xef\xbb\xbf"), re.M | re.I)
    if match:
        name = match.group(1).strip().lower().replace("_", "-")
        codec = {"utf8": "utf-8-sig", "utf-8": "utf-8-sig"}.get(name, name)
        try:
            return raw.decode(codec)
        except (LookupError, ValueError):
            pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _to_float(value: str) -> float | None:
    try:
        result = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def parse_ultrastar(raw: bytes) -> UltraStarSong | None:
    """Parse an UltraStar file; None without ``#BPM``, artist/title or singable notes.

    Never raises: malformed input of any kind yields None.
    """
    try:
        return _parse(raw)
    except Exception:
        return None


def _parse(raw: bytes) -> UltraStarSong | None:
    text = _decode(raw)
    headers: dict[str, str] = {}
    parts: dict[str, list[UltraStarNote]] = {}
    current = "all"
    base = 0
    pending: list[tuple[str, UltraStarNote]] = []

    # Headers first (they may follow notes in sloppy files, so collect all).
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") and ":" in line:
            key, _, value = line[1:].partition(":")
            headers.setdefault(key.strip().upper(), value.strip())
    relative = headers.get("RELATIVE", "").lower() == "yes"

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "E":
            break
        part = _PART_RE.match(line)
        if part:
            current = f"P{part.group(1)}"
            base = 0
            continue
        fields = line.split(None, 4)
        kind = fields[0]
        try:
            if kind == "-":
                if relative and len(fields) >= 3:
                    base += int(fields[2])
                continue
            if kind not in SINGABLE_KINDS and kind not in {"F", "R", "G"}:
                continue
            beat, length, pitch = int(fields[1]), int(fields[2]), int(fields[3])
        except (ValueError, IndexError):
            continue
        if kind in SINGABLE_KINDS and length > 0:
            pending.append((current, UltraStarNote(base + beat if relative else beat, length, pitch, kind)))

    for name, note in pending:
        parts.setdefault(name, []).append(note)

    bpm = _to_float(headers.get("BPM", ""))
    artist, title = headers.get("ARTIST", ""), headers.get("TITLE", "")
    if not bpm or bpm <= 0 or not artist or not title or not parts:
        return None

    video = _VIDEO_ID_RE.search(headers.get("VIDEO", ""))
    year_text = headers.get("YEAR", "")
    return UltraStarSong(
        artist=artist,
        title=title,
        language=headers.get("LANGUAGE") or None,
        year=int(year_text) if _YEAR_RE.match(year_text) else None,
        video_id=video.group(1) if video else None,
        bpm=bpm,
        gap_ms=_to_float(headers.get("GAP", "")) or 0.0,
        relative=relative,
        parts=parts,
    )


def _weighted_percentile(sorted_pairs: list[tuple[int, int]], pct: int, total: int) -> int:
    """Nearest rank on cumulative weight.

    ``sorted_pairs`` is ``(midi, weight)`` ascending by midi. The percentile is the
    first midi whose cumulative weight is >= pct/100 * total (compared in integers,
    ``cum * 100 >= pct * total``, so there is no float rounding).
    """
    cum = 0
    for midi, weight in sorted_pairs:
        cum += weight
        if cum * 100 >= pct * total:
            return midi
    return sorted_pairs[-1][0]


def note_stats(notes: list[UltraStarNote]) -> NoteStats | None:
    """Duration-weighted (by note length) pitch statistics in MIDI; None if no notes."""
    pairs = sorted((n.pitch + MIDI_OFFSET, n.length) for n in notes if n.length > 0)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    low = _weighted_percentile(pairs, 5, total)
    high = _weighted_percentile(pairs, 95, total)
    return NoteStats(
        low=float(low),
        high=float(high),
        p10=float(_weighted_percentile(pairs, 10, total)),
        p50=float(_weighted_percentile(pairs, 50, total)),
        p90=float(_weighted_percentile(pairs, 90, total)),
        span=float(high - low),
        sung_beats=total,
        raw_min=float(pairs[0][0]),
        raw_max=float(pairs[-1][0]),
    )
