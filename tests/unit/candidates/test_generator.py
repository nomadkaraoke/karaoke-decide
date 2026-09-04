"""Integration tests for the v3 candidate pipeline (all deps faked)."""

from unittest.mock import MagicMock

import pytest

from karaoke_decide.candidates.generator import CandidateGenerator
from karaoke_decide.candidates.matching import canonical_key
from karaoke_decide.services.flacfetch import FlacfetchClient
from karaoke_decide.services.llm_judge import Verdict
from karaoke_decide.services.spotify_features import SpotifyFeatures


def _rich_text(n_lines: int = 15) -> str:
    return "\n".join(f"word{3 * i} word{3 * i + 1} word{3 * i + 2}" for i in range(n_lines))


def _feat(inst=0.0, dur_min=4.0):
    return SpotifyFeatures(
        instrumentalness=inst,
        speechiness=0.05,
        energy=0.8,
        valence=0.5,
        danceability=0.6,
        tempo=174.0,
        duration_ms=int(dur_min * 60000),
        popularity=30,
        explicit=False,
    )


class FakeLastFm:
    def __init__(self, tracks):
        self._tracks = tracks

    async def get_all_top_tracks(self, username, period="overall", max_tracks=5000):
        return [{"artist": t["artist"], "name": t["title"], "playcount": t["playcount"]} for t in self._tracks]


class FakeLrclib:
    def __init__(self, lyrics_by_title):
        self._lyrics = lyrics_by_title

    async def best_lyrics(self, artist, title):
        text = self._lyrics.get(title)
        return None if text is None else {"plain": text, "synced": "", "instrumental": False}


class FakeFlac:
    def __init__(self, sourceable):
        self._sourceable = set(sourceable)

    async def search(self, artist, title, exhaustive=False):
        if title in self._sourceable:
            return [
                {
                    "provider": "RED",
                    "is_lossless": True,
                    "seeders": 50,
                    "match_score": 1.0,
                    "release_type": "Album",
                    "quality_data": {"format": "FLAC", "bit_depth": 16},
                }
            ]
        return []

    best_flac = staticmethod(FlacfetchClient.best_flac)


class FakeSpotify:
    def __init__(self, features_by_title):
        self._by_title = features_by_title

    def lookup(self, tracks):
        return {(a, t): self._by_title[t] for a, t in tracks if t in self._by_title}


class FakeLlm:
    def __init__(self, reject_titles):
        self._reject = set(reject_titles)
        self.calls = 0

    def judge(self, artist, title, lyrics, metadata):
        self.calls += 1
        keep = title not in self._reject
        return Verdict(keep=keep, confidence=0.9, reason="ok" if keep else "instrumental")


class FakeCatalog:
    PROJECT_ID = "test"
    DATASET_ID = "test"

    def __init__(self, rows):
        self.client = MagicMock()
        self.client.query.return_value.result.return_value = [{"Artist": a, "Title": t} for a, t in rows]


class FakeGenJobs:
    def __init__(self, keys):
        self._keys = keys

    def produced_keys(self):
        return self._keys


@pytest.fixture
def generator(tmp_path):
    tracks = [
        {"artist": "Made", "title": "MadeSong", "playcount": 100},
        {"artist": "Community", "title": "CommSong", "playcount": 95},
        {"artist": "Rejected", "title": "RejSong", "playcount": 90},
        {"artist": "NoSpotify", "title": "NoSpotSong", "playcount": 85},
        {"artist": "Good", "title": "GoodSong", "playcount": 80},
        {"artist": "NoLyrics", "title": "InstSong", "playcount": 75},
        {"artist": "LowScore", "title": "LowSong", "playcount": 70},
        {"artist": "LlmReject", "title": "LlmSong", "playcount": 65},
        {"artist": "Unsourced", "title": "UnsrcSong", "playcount": 60},
    ]
    features = {
        "GoodSong": _feat(inst=0.0),
        "InstSong": _feat(inst=0.0),
        "LowSong": _feat(inst=0.98),
        "LlmSong": _feat(inst=0.0),
        "UnsrcSong": _feat(inst=0.0),
    }
    gen = CandidateGenerator(
        base_dir=tmp_path,
        lastfm=FakeLastFm(tracks),
        lrclib=FakeLrclib(
            {
                "GoodSong": _rich_text(15),
                "LowSong": "one line",
                "LlmSong": _rich_text(15),
                "UnsrcSong": _rich_text(15),
            }
        ),
        flacfetch=FakeFlac(sourceable={"GoodSong"}),
        gen_jobs=FakeGenJobs({canonical_key("Made", "MadeSong")}),
        catalog=FakeCatalog([("Community", "CommSong")]),
        spotify=FakeSpotify(features),
        llm=FakeLlm(reject_titles={"LlmSong"}),
        username="tester",
        flacfetch_min_interval=0.0,
        lrclib_min_interval=0.0,
    )
    gen.rejects.add("Rejected", "RejSong", "not good", "2026-08-31")
    return gen


class TestSuggest:
    async def test_confirms_only_good_song(self, generator):
        result = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert [c.title for c in result.confirmed] == ["GoodSong"]

    async def test_skip_reasons(self, generator):
        r = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert r.skipped["already_ours"] == 1
        assert r.skipped["community_version"] == 1
        assert r.skipped["rejected"] == 1
        assert r.skipped["no_spotify_features"] == 1
        assert r.skipped["no_lrclib_lyrics"] == 1
        assert r.skipped["low_score"] == 1
        assert r.skipped["llm_reject"] == 1
        assert r.skipped["unsourceable"] == 1

    async def test_llm_only_called_after_cheaper_gates(self, generator):
        await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert generator.llm.calls == 3  # GoodSong, LlmSong, UnsrcSong

    async def test_confirmed_carries_features_and_score(self, generator):
        r = await generator.suggest(count=5, min_plays=1, max_checks=50)
        c = r.confirmed[0]
        assert c.features.instrumentalness == 0.0
        assert c.score > 80
        assert c.llm["keep"] is True

    async def test_llm_reject_and_unsourceable_recorded_as_miss(self, generator):
        r = await generator.suggest(count=5, min_plays=1, max_checks=50)
        reasons = {m.title: m.reason for m in r.misses}
        assert "LlmSong" in reasons and reasons["LlmSong"].startswith("llm:")
        assert reasons.get("UnsrcSong") == "unsourceable"

    async def test_caching_skips_repeat_llm(self, generator):
        await generator.suggest(count=5, min_plays=1, max_checks=50)
        first_calls = generator.llm.calls
        generator.llm._reject = set()
        r = await generator.suggest(count=5, min_plays=1, max_checks=50)
        assert generator.llm.calls == first_calls
        assert [c.title for c in r.confirmed] == ["GoodSong"]

    async def test_write_reports(self, generator):
        r = await generator.suggest(count=5, min_plays=1, max_checks=50)
        paths = generator.write_reports(r)
        assert paths["csv"].read_text().startswith("artist,title,playcount,score")
        assert "GoodSong" in paths["json"].read_text()
        assert "UnsrcSong" in paths["misses"].read_text()


class TestSingable:
    """singable() lists played tracks that ALREADY have a community version."""

    def _seed_community(self, generator, rows):
        # Prime the blob cache so load_karaokenerds_community_index skips BigQuery.
        generator.cache.set_blob("karaokenerds_community", rows)

    async def test_lists_matches_in_playcount_order(self, generator):
        self._seed_community(
            generator,
            [
                ["Good", "GoodSong", "WTF", "https://youtu.be/g2"],
                ["Good", "GoodSong", "KV", ""],
                ["Community", "CommSong", "NOMAD", "https://youtu.be/c1"],
            ],
        )
        result = await generator.singable(count=50, min_plays=1)
        assert [s.title for s in result.songs] == ["CommSong", "GoodSong"]
        assert result.considered == 9
        assert result.matched == 2

    async def test_enriches_brands_watch_and_version_count(self, generator):
        self._seed_community(
            generator,
            [
                ["Good", "GoodSong", "WTF", "https://youtu.be/g2"],
                ["Good", "GoodSong", "KV", ""],
            ],
        )
        result = await generator.singable(count=50, min_plays=1)
        song = result.songs[0]
        assert song.title == "GoodSong"
        assert song.brands == ["KV", "WTF"]  # sorted, deduped
        assert song.version_count == 2
        assert song.watch == "https://youtu.be/g2"  # first non-empty watch

    async def test_count_caps_collected_but_keeps_counting_matched(self, generator):
        self._seed_community(
            generator,
            [
                ["Community", "CommSong", "NOMAD", "https://youtu.be/c1"],
                ["Good", "GoodSong", "WTF", "https://youtu.be/g2"],
            ],
        )
        result = await generator.singable(count=1, min_plays=1)
        assert [s.title for s in result.songs] == ["CommSong"]
        assert result.matched == 2

    async def test_min_plays_filters_before_matching(self, generator):
        self._seed_community(generator, [["Good", "GoodSong", "WTF", "https://youtu.be/g2"]])
        # GoodSong has 80 plays; a threshold above it removes it entirely.
        result = await generator.singable(count=50, min_plays=90)
        assert result.songs == []

    async def test_no_matches_when_catalog_empty(self, generator):
        self._seed_community(generator, [])
        result = await generator.singable(count=50, min_plays=1)
        assert result.songs == [] and result.matched == 0 and result.considered == 9

    async def test_write_singable_reports(self, generator):
        self._seed_community(
            generator,
            [["Community", "CommSong", "NOMAD", "https://youtu.be/c1"]],
        )
        result = await generator.singable(count=50, min_plays=1)
        paths = generator.write_singable_reports(result)
        assert paths["csv"].read_text().startswith("playcount,artist,title,brands")
        assert "CommSong" in paths["json"].read_text()
        assert "youtu.be/c1" in paths["md"].read_text()

    async def test_write_singable_reports_escapes_markdown_pipes(self, generator, tmp_path):
        # A pipe in a title must not spawn phantom Markdown table columns.
        from karaoke_decide.candidates.generator import SingableResult, SingableSong

        result = SingableResult(
            songs=[SingableSong("A|B", "Song | Remix", 10, ["NOMAD"], None, 1)],
            considered=1,
            matched=1,
        )
        paths = generator.write_singable_reports(result)
        md = paths["md"].read_text()
        row = next(line for line in md.splitlines() if "Song" in line and line.startswith("|"))
        # 6 columns => 7 pipes when none are stray; escaped pipes are "\|".
        assert row.count("|") - row.count("\\|") == 7
        assert "Song \\| Remix" in md
