"""Integration tests for the candidate pipeline (all deps faked)."""

from unittest.mock import MagicMock

import pytest

from karaoke_decide.candidates.generator import CandidateGenerator
from karaoke_decide.candidates.matching import canonical_key
from karaoke_decide.services.flacfetch import FlacfetchClient


def _rich_text(n_lines: int = 15) -> str:
    # 3 distinct words per line -> >=30 unique words, >=10 unique lines.
    return "\n".join(
        f"word{3 * i} word{3 * i + 1} word{3 * i + 2}" for i in range(n_lines)
    )


class FakeLastFm:
    def __init__(self, tracks, electronic_artists=()):
        self._tracks = tracks
        self._electronic = set(electronic_artists)

    async def get_all_top_tracks(self, username, period="overall", max_tracks=5000):
        return [
            {"artist": t["artist"], "name": t["title"], "playcount": t["playcount"]}
            for t in self._tracks
        ]

    async def get_artist_top_tags(self, artist):
        return ["drum and bass"] if artist in self._electronic else ["rock"]


class FakeLrclib:
    def __init__(self, lyrics_by_title):
        self._lyrics = lyrics_by_title

    async def best_lyrics(self, artist, title):
        text = self._lyrics.get(title)
        if text is None:
            return None
        return {"plain": text, "synced": "", "instrumental": False}


class FakeFlac:
    def __init__(self, sourceable_titles):
        self._sourceable = set(sourceable_titles)

    async def search(self, artist, title, exhaustive=False):
        if title in self._sourceable:
            return [
                {
                    "provider": "RED", "is_lossless": True, "seeders": 50,
                    "match_score": 1.0, "release_type": "Album",
                    "quality_data": {"format": "FLAC", "bit_depth": 16},
                }
            ]
        return []

    best_flac = staticmethod(FlacfetchClient.best_flac)


class FakeCatalog:
    PROJECT_ID = "test"
    DATASET_ID = "test"

    def __init__(self, rows):
        self.client = MagicMock()
        self.client.query.return_value.result.return_value = [
            {"Artist": a, "Title": t} for a, t in rows
        ]


class FakeGenJobs:
    def __init__(self, keys):
        self._keys = keys

    def produced_keys(self):
        return self._keys


@pytest.fixture
def generator(tmp_path):
    tracks = [
        {"artist": "Made", "title": "MadeSong", "playcount": 100},
        {"artist": "Community", "title": "CommSong", "playcount": 90},
        {"artist": "Rejected", "title": "RejSong", "playcount": 85},
        {"artist": "Good", "title": "GoodSong", "playcount": 80},
        {"artist": "NoLyrics", "title": "InstSong", "playcount": 70},
        {"artist": "Poor", "title": "SparseSong", "playcount": 60},
        {"artist": "Unsourced", "title": "UnsrcSong", "playcount": 50},
    ]
    gen = CandidateGenerator(
        base_dir=tmp_path,
        lastfm=FakeLastFm(tracks),
        lrclib=FakeLrclib(
            {
                "GoodSong": _rich_text(15),
                "SparseSong": "one line only",
                "UnsrcSong": _rich_text(15),
                # InstSong -> None (no lyrics)
            }
        ),
        flacfetch=FakeFlac(sourceable_titles={"GoodSong"}),
        gen_jobs=FakeGenJobs({canonical_key("Made", "MadeSong")}),
        catalog=FakeCatalog([("Community", "CommSong")]),
        username="tester",
        flacfetch_min_interval=0.0,
        lrclib_min_interval=0.0,
    )
    # Reject one song.
    gen.rejects.add("Rejected", "RejSong", "not good", "2026-08-30")
    return gen


class TestSuggest:
    async def test_confirms_only_the_good_song(self, generator):
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        titles = [c.title for c in result.confirmed]
        assert titles == ["GoodSong"]

    async def test_skip_reasons(self, generator):
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert result.skipped["already_ours"] == 1
        assert result.skipped["community_version"] == 1
        assert result.skipped["rejected"] == 1
        assert result.skipped["no_lrclib_lyrics"] == 1
        assert result.skipped["too_few_lines"] == 1
        assert result.skipped["unsourceable"] == 1

    async def test_unsourceable_recorded_as_miss(self, generator):
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert [m.title for m in result.misses] == ["UnsrcSong"]

    async def test_stops_at_count(self, generator):
        result = await generator.suggest(count=1, min_plays=1, max_checks=50)
        assert len(result.confirmed) == 1

    async def test_min_plays_filter(self, generator):
        result = await generator.suggest(count=5, min_plays=95, max_checks=50)
        # Only "Made" (100) survives min_plays but it's already ours.
        assert result.confirmed == []

    async def test_caching_avoids_second_lyrics_call(self, generator):
        await generator.suggest(count=5, min_plays=1, max_checks=50)
        # Second run: lyrics come from cache, so a broken client shouldn't matter.
        generator.lrclib = None  # type: ignore[assignment]
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert [c.title for c in result.confirmed] == ["GoodSong"]

    async def test_calibrate_reports_lyrics_presence(self, generator):
        rows = await generator.calibrate(sample=10, min_plays=1)
        by_title = {r["title"]: r for r in rows}
        # Cheap-filtered songs (already ours / community / rejected) are excluded.
        assert "MadeSong" not in by_title
        assert "CommSong" not in by_title
        assert by_title["GoodSong"]["has_lyrics"] is True
        assert by_title["GoodSong"]["stats"].unique_lines >= 10
        assert by_title["InstSong"]["has_lyrics"] is False

    async def test_calibrate_respects_sample_cap(self, generator):
        rows = await generator.calibrate(sample=1, min_plays=1)
        assert len(rows) == 1

    async def test_write_reports(self, generator, tmp_path):
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        paths = generator.write_reports(result)
        assert paths["json"].exists()
        assert "GoodSong" in paths["json"].read_text()
        assert paths["misses"].exists()
        assert "UnsrcSong" in paths["misses"].read_text()

    async def test_empty_result_still_writes_csv_header(self, generator):
        # No survivors (impossible min_plays) -> CSV must be rewritten, not stale.
        result = await generator.suggest(count=5, min_plays=10_000, max_checks=50)
        paths = generator.write_reports(result)
        text = paths["csv"].read_text()
        assert text.startswith("artist,title,playcount")
        assert len(text.strip().splitlines()) == 1  # header only

    async def test_csv_escapes_formula_injection(self, generator):
        from karaoke_decide.candidates.generator import Candidate

        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        result.confirmed.append(
            Candidate(
                artist="=cmd()",
                title="+evil",
                playcount=1,
                is_electronic=False,
                stats=result.confirmed[0].stats,
                flac={"provider": "RED", "seeders": 1},
            )
        )
        paths = generator.write_reports(result)
        text = paths["csv"].read_text()
        assert "'=cmd()" in text
        assert "'+evil" in text
