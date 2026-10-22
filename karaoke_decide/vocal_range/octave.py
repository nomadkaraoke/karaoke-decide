"""Octave repair for UltraStar note stats, and the octave-error rate used to validate it."""

from __future__ import annotations

import dataclasses
from typing import Literal

from karaoke_decide.vocal_range.ultrastar import NoteStats

# (low, high) MIDI comfortable lead register by gender.
REGISTERS: dict[str | None, tuple[int, int]] = {
    "male": (45, 69),
    "female": (52, 76),
    None: (45, 76),
}
_SHIFTS = (-24, -12, 0, 12, 24)
_MARGIN = 2
_OCTAVE_ERROR_SEMITONES = 9.0


def _normalise_gender(lead_gender: str | None) -> Literal["male", "female"] | None:
    value = lead_gender.strip().lower() if isinstance(lead_gender, str) else None
    return "male" if value == "male" else "female" if value == "female" else None


def repair_octave(stats: NoteStats, lead_gender: str | None) -> tuple[int, float]:
    """Pick the octave shift (-24/-12/0/12/24) that best fits the lead's register.

    Minimises ``|p50 + s - centre(register)|``; ties go to 0. Returns ``(shift,
    octave_confidence)``: 0.9 if exactly one shift keeps ``[p10+s, p90+s]`` within
    the register +-2 semitones, 0.5 if several do, 0.3 if none; x0.6 when the
    gender is unknown. ``lead_gender`` is case-insensitive; anything other than
    male/female counts as unknown.
    """
    lead_gender = _normalise_gender(lead_gender)
    low, high = REGISTERS[lead_gender]
    centre = (low + high) / 2
    # min() keeps the first minimum, so order candidates with 0 first for ties.
    shift = min((0, -12, 12, -24, 24), key=lambda s: abs(stats.p50 + s - centre))
    inside = [s for s in _SHIFTS if stats.p10 + s >= low - _MARGIN and stats.p90 + s <= high + _MARGIN]
    confidence = 0.9 if len(inside) == 1 else 0.5 if len(inside) > 1 else 0.3
    if lead_gender is None:
        confidence *= 0.6
    return shift, confidence


def shifted(stats: NoteStats, shift: int) -> NoteStats:
    """All pitch fields moved by ``shift`` semitones (span and beats unchanged)."""
    return dataclasses.replace(
        stats,
        low=stats.low + shift,
        high=stats.high + shift,
        p10=stats.p10 + shift,
        p50=stats.p50 + shift,
        p90=stats.p90 + shift,
        raw_min=stats.raw_min + shift,
        raw_max=stats.raw_max + shift,
    )


def octave_error_rate(pairs: list[tuple[float, float]]) -> tuple[float, int]:
    """Share of ``(repaired p50, measured p50)`` pairs that differ by >= 9 semitones."""
    n = len(pairs)
    if n == 0:
        return 0.0, 0
    errors = sum(1 for repaired, measured in pairs if abs(repaired - measured) >= _OCTAVE_ERROR_SEMITONES)
    return errors / n, n
