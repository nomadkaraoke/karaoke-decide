"""Tests for lyric-richness heuristics."""

from karaoke_decide.candidates.lyrics import (
    RichnessThresholds,
    analyze,
    clean_lyrics,
    is_rich,
)

SYNCED = """[ar:Artist]
[ti:Title]
[00:12.34] Line one here
[00:15.00] Line two here
[00:18.00] Line one here
"""


class TestCleanLyrics:
    def test_strips_timestamps_and_metadata(self):
        cleaned = clean_lyrics(SYNCED)
        assert "[00:12.34]" not in cleaned
        assert "[ar:Artist]" not in cleaned
        assert "Line one here" in cleaned

    def test_strips_section_markers(self):
        assert "Verse" not in clean_lyrics("[Verse 1]\nHello world")

    def test_empty_input(self):
        assert clean_lyrics("") == ""


class TestAnalyze:
    def test_counts_unique_lines_and_words(self):
        stats = analyze(SYNCED)
        assert stats.total_lines == 3
        assert stats.unique_lines == 2  # "line one here" repeated
        assert stats.unique_words == 4  # line, one, two, here

    def test_instrumental_zero_words(self):
        stats = analyze("")
        assert stats.total_words == 0
        assert stats.unique_line_ratio == 0.0


def _rich_stats(lines: int, words: int = 0, unique_lines: int | None = None):
    # Each line has 3 distinct words -> plenty of unique words and lines.
    text = "\n".join(f"word{3 * i} word{3 * i + 1} word{3 * i + 2}" for i in range(lines))
    return analyze(text)


class TestIsRich:
    def test_instrumental_rejected(self):
        passed, reason = is_rich(analyze(""), RichnessThresholds())
        assert not passed and reason == "instrumental"

    def test_non_electronic_passes_with_enough_lines(self):
        stats = _rich_stats(15, 60)
        passed, _ = is_rich(stats, RichnessThresholds(), is_electronic=False)
        assert passed

    def test_non_electronic_rejected_too_few_lines(self):
        stats = _rich_stats(3, 40)
        passed, reason = is_rich(stats, RichnessThresholds(), is_electronic=False)
        assert not passed and reason == "too_few_lines"

    def test_electronic_stricter_line_count(self):
        # 11 unique lines passes non-electronic (>=10) but not electronic (>=12).
        stats = _rich_stats(11, 60)
        assert is_rich(stats, RichnessThresholds(), is_electronic=False)[0]
        passed, reason = is_rich(stats, RichnessThresholds(), is_electronic=True)
        assert not passed and reason == "electronic_too_few_lines"

    def test_electronic_repetition_rejected(self):
        # Many lines but mostly duplicates -> low unique-line ratio.
        text = "\n".join(["same repeated line"] * 30 + [f"x{i}" for i in range(13)])
        stats = analyze(text)
        passed, reason = is_rich(stats, RichnessThresholds(), is_electronic=True)
        assert not passed and reason == "electronic_too_repetitive"
