"""Manual reject list — songs Andrew has looked at and decided *not* to make.

Stored as a committed, hand-editable JSONL file (one JSON object per line) so
it diffs cleanly and travels with the repo. Each entry records the reason and
date, which we periodically review to improve the automatic heuristics.

Matching is normalized (same canonical key as everything else) so a reject
survives minor spelling/casing differences between the reject entry and the
Last.fm track name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .matching import canonical_key


@dataclass(frozen=True)
class RejectEntry:
    artist: str
    title: str
    reason: str
    date: str

    def key(self) -> tuple[str, str]:
        return canonical_key(self.artist, self.title)


class RejectList:
    """Load/append the committed reject-list JSONL file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[RejectEntry]:
        if not self.path.exists():
            return []
        entries: list[RejectEntry] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Hand-edited files can contain malformed rows; require a JSON object
            # with string artist/title, and skip anything else rather than crash.
            if not isinstance(obj, dict):
                continue
            artist = obj.get("artist", "")
            title = obj.get("title", "")
            if not isinstance(artist, str) or not isinstance(title, str):
                continue
            if not artist or not title:
                continue
            reason = obj.get("reason", "")
            date = obj.get("date", "")
            entries.append(
                RejectEntry(
                    artist=artist,
                    title=title,
                    reason=reason if isinstance(reason, str) else "",
                    date=date if isinstance(date, str) else "",
                )
            )
        return entries

    def key_set(self) -> set[tuple[str, str]]:
        return {e.key() for e in self.load()}

    def add(self, artist: str, title: str, reason: str, date: str) -> RejectEntry:
        """Append a reject entry (idempotent on canonical key)."""
        entry = RejectEntry(artist=artist, title=title, reason=reason, date=date)
        existing = self.key_set()
        if entry.key() in existing:
            return entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(
                json.dumps(
                    {"artist": artist, "title": title, "reason": reason, "date": date}
                )
                + "\n"
            )
        return entry
