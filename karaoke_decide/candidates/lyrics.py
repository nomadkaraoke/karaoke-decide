"""Lyric-richness heuristics.

Given raw LRCLIB lyrics text (synced ``[mm:ss.xx]`` LRC or plain), decide
whether a song has enough *singable* content to make a good karaoke candidate,
versus a repetitive EDM track or an instrumental/interlude with almost no
words.

The thresholds here are provisional defaults — the ``calibrate`` CLI command
exists specifically to tune them against Andrew's real library before we trust
them. Electronic/instrumental-leaning tracks are held to a stricter bar (his
library is heavy on drum & bass / electronic, where many "tracks" are drops
with a one-line hook).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_LRC_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
_LRC_META = re.compile(r"^\[[a-z]+:.*\]$", re.I)  # [ar:...], [ti:...], [length:...]
_SECTION = re.compile(r"\[[^\]]*\]")  # [Verse 1], [Chorus]
_WORD = re.compile(r"[a-z0-9']+", re.I)


@dataclass(frozen=True)
class LyricsStats:
    """Summary statistics for a set of lyrics."""

    total_lines: int
    unique_lines: int
    total_words: int
    unique_words: int

    @property
    def unique_line_ratio(self) -> float:
        return self.unique_lines / self.total_lines if self.total_lines else 0.0

    @property
    def unique_word_ratio(self) -> float:
        return self.unique_words / self.total_words if self.total_words else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["unique_line_ratio"] = round(self.unique_line_ratio, 3)
        d["unique_word_ratio"] = round(self.unique_word_ratio, 3)
        return d


@dataclass(frozen=True)
class RichnessThresholds:
    """Gate thresholds. Defaults are provisional (see module docstring)."""

    min_unique_lines: int = 10
    min_unique_words: int = 30
    # Electronic tracks: stricter, and must not be too repetitive.
    electronic_min_unique_lines: int = 12
    electronic_min_unique_line_ratio: float = 0.40


def clean_lyrics(text: str) -> str:
    """Strip LRC timestamps and metadata/section markers, returning plain text."""
    if not text:
        return ""
    text = _LRC_TIMESTAMP.sub("", text)
    out_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _LRC_META.match(line):
            continue
        line = _SECTION.sub(" ", line).strip()
        if line:
            out_lines.append(line)
    return "\n".join(out_lines)


def analyze(text: str) -> LyricsStats:
    """Compute richness statistics from raw (synced or plain) lyrics text."""
    cleaned = clean_lyrics(text)
    lines = [ln.strip().lower() for ln in cleaned.splitlines() if ln.strip()]
    words = _WORD.findall(cleaned.lower())
    return LyricsStats(
        total_lines=len(lines),
        unique_lines=len(set(lines)),
        total_words=len(words),
        unique_words=len(set(words)),
    )


def is_rich(
    stats: LyricsStats,
    thresholds: RichnessThresholds,
    is_electronic: bool = False,
) -> tuple[bool, str]:
    """Return (passes, reason). ``reason`` explains a failure (empty if passed)."""
    if stats.total_words == 0:
        return False, "instrumental"

    if is_electronic:
        if stats.unique_lines < thresholds.electronic_min_unique_lines:
            return False, "electronic_too_few_lines"
        if stats.unique_line_ratio < thresholds.electronic_min_unique_line_ratio:
            return False, "electronic_too_repetitive"
        return True, ""

    if stats.unique_lines < thresholds.min_unique_lines:
        return False, "too_few_lines"
    if stats.unique_words < thresholds.min_unique_words:
        return False, "too_few_words"
    return True, ""
