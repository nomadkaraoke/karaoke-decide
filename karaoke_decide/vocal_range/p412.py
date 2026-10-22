"""Wikidata P412 (voice type) -> our six voice types. Wikidata is CC0."""

from __future__ import annotations

import csv
from pathlib import Path

# Every label with >= 9 rows in data/vocal_range/wikidata_p412.csv (2026-10-21 export).
# None = not a pitch class we can use (gender-only, technique, or castrato).
P412_MAP: dict[str, str | None] = {
    "soprano": "soprano",
    "coloratura soprano": "soprano",
    "lyric soprano": "soprano",
    "dramatic soprano": "soprano",
    "light soprano": "soprano",
    "Soubrette": "soprano",
    "mezzo-soprano": "mezzo",
    "lyric mezzo-soprano": "mezzo",
    "light mezzo-soprano": "mezzo",
    "contralto": "alto",
    "alto": "alto",
    "countertenor": "alto",
    "tenor": "tenor",
    "Heldentenor": "tenor",
    "tenore di grazia": "tenor",
    "lyric tenor": "tenor",
    "light tenor": "tenor",
    "dramatic tenor": "tenor",
    "Tenore contraltino": "tenor",
    "haute-contre": "tenor",
    "baritone": "baritone",
    "lyric baritone": "baritone",
    "baritenor": "baritone",
    "baryton-Martin": "baritone",
    "bass": "bass",
    "bass-baritone": "bass",
    "basso profondo": "bass",
    "male voice": None,
    "death growl": None,
    "castrato": None,
}


def load_p412(path: str | Path) -> dict[str, set[str]]:
    """``mbid -> {voice types}``; unmapped / None labels are dropped, and so are mbids left empty."""
    out: dict[str, set[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vt = P412_MAP.get(row["vtLabel"])
            if vt:
                out.setdefault(row["mbid"], set()).add(vt)
    return out
