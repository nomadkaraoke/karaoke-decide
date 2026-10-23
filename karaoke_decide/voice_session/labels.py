"""Andrew's sheet labels: parse good?/Notes columns into evidence for the voice model."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Literal

LabelTag = Literal[
    "needs_warmup",
    "good_if_warm",
    "mood_only",
    "octave_split",
    "section_octave_jump",
    "key_change",
    "needs_practice",
    "no_intro",
    "version_good",
]

_YES = {"y", "yes", "good", "✓", "✔", "1"}
_NO = {"n", "no", "x", "✗", "0"}

_NOTE_RULES: tuple[tuple[LabelTag, str], ...] = (
    ("needs_warmup", r"warm-?up|warmed up"),
    ("good_if_warm", r"if warm"),
    ("mood_only", r"flamboyant|in the mood|mood"),
    ("octave_split", r"too low if .* too high"),
    ("section_octave_jump", r"octave is mixed|verse and chorus"),
    ("key_change", r"lower octave|raise the key|\bkey\b"),
    ("needs_practice", r"practi[cs]e"),
    ("no_intro", r"no intro|comes in immediately"),
    ("version_good", r"version is great|version is good"),
)
_NOTE_PATTERNS = tuple((tag, re.compile(rx, re.IGNORECASE)) for tag, rx in _NOTE_RULES)


def parse_good(value: str | None) -> Literal["yes", "no", "maybe"] | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in _YES:
        return "yes"
    if v in _NO:
        return "no"
    return "maybe"


def parse_note(note: str | None) -> set[LabelTag]:
    text = note or ""
    return {tag for tag, rx in _NOTE_PATTERNS if rx.search(text)}


@dataclass
class SheetRow:
    artist: str
    title: str
    duet: bool | None
    good: str | None
    sung: bool | None
    notes: str


def _flag(value: str) -> bool | None:
    v = value.strip().lower()
    if not v:
        return None
    return v in {"y", "yes", "true", "1", "✓", "✔", "x"}


def rows_from_csv(text: str) -> list[SheetRow]:
    reader = csv.DictReader(io.StringIO(text))
    fields = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}

    def col(row: dict[str, str], name: str) -> str:
        key = fields.get(name)
        return (row.get(key) or "").strip() if key is not None else ""

    rows: list[SheetRow] = []
    for raw in reader:
        artist, title = col(raw, "artist"), col(raw, "title")
        if not artist or not title:
            continue
        rows.append(
            SheetRow(
                artist=artist,
                title=title,
                duet=_flag(col(raw, "duet?")),
                good=col(raw, "good?") or None,
                sung=_flag(col(raw, "sung?")),
                notes=col(raw, "notes"),
            )
        )
    return rows
