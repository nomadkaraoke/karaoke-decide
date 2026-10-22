"""MIDI/note-name helpers. Mirrors nomad-mobile ``packages/core/src/voice/notes.ts``."""

from __future__ import annotations

import math
import re

_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])([#♯b♭]?)(-?\d+)$")

# (low, high) MIDI range per voice type, in this order.
VOICE_TYPES: dict[str, tuple[int, int]] = {
    "bass": (40, 64),
    "baritone": (45, 69),
    "tenor": (48, 72),
    "alto": (53, 77),
    "mezzo": (57, 81),
    "soprano": (60, 84),
}


def hz_to_midi(hz: float) -> float:
    """MIDI as a float: A4 = 440 Hz = 69, C4 = 60."""
    return 69 + 12 * math.log2(hz / 440)


def midi_to_name(m: float) -> str:
    """Rounded name with sharps, e.g. 46 -> "A#2". Half rounds up (like JS Math.round)."""
    n = math.floor(m + 0.5)
    return f"{_NAMES[n % 12]}{n // 12 - 1}"


def note_name_to_midi(name: str) -> int | None:
    """Parse "C#4", "E♭3", "Bb2"; None when it is not a note name."""
    match = _NOTE_RE.match(name.strip())
    if not match:
        return None
    letter, accidental, octave = match.groups()
    acc = 1 if accidental in ("#", "♯") else -1 if accidental in ("b", "♭") else 0
    return (int(octave) + 1) * 12 + _SEMITONE[letter.upper()] + acc
