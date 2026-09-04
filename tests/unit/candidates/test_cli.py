"""CLI tests for the candidates command group (no network)."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from karaoke_decide.candidates.generator import (
    Candidate,
    SingableResult,
    SingableSong,
    SuggestResult,
)
from karaoke_decide.candidates.lyrics import analyze
from karaoke_decide.cli.main import cli
from karaoke_decide.services.spotify_features import SpotifyFeatures


class TestRejectCommands:
    def test_reject_and_review(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANDIDATES_DIR", str(tmp_path))
        runner = CliRunner()
        res = runner.invoke(cli, ["candidates", "reject", "Pendulum", "Slam", "--reason", "too repetitive"])
        assert res.exit_code == 0, res.output
        assert "Rejected" in res.output

        res = runner.invoke(cli, ["candidates", "review-rejects"])
        assert res.exit_code == 0
        assert "Pendulum" in res.output and "too repetitive" in res.output

    def test_review_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANDIDATES_DIR", str(tmp_path))
        res = CliRunner().invoke(cli, ["candidates", "review-rejects"])
        assert res.exit_code == 0
        assert "No rejects" in res.output

    def test_help_lists_subcommands(self):
        res = CliRunner().invoke(cli, ["candidates", "--help"])
        assert res.exit_code == 0
        for cmd in ("suggest", "singable", "reject", "review-rejects"):
            assert cmd in res.output


def _feat():
    return SpotifyFeatures(
        instrumentalness=0.05,
        speechiness=0.05,
        energy=0.8,
        valence=0.5,
        danceability=0.6,
        tempo=174.0,
        duration_ms=210000,
        popularity=40,
        explicit=False,
    )


class TestSuggestCommand:
    def _fake_gen(self):
        cand = Candidate(
            artist="Pendulum",
            title="Slam",
            playcount=88,
            stats=analyze("line one\nline two\nline three"),
            features=_feat(),
            score=92.0,
            llm={"keep": True, "confidence": 0.9, "reason": "vocal-forward"},
            flac={"provider": "RED", "seeders": 388, "bit_depth": 16},
        )
        result = SuggestResult(confirmed=[cand], considered=1, no_karaoke=1)
        gen = MagicMock()
        gen.suggest = AsyncMock(return_value=result)
        gen.write_reports.return_value = {
            "csv": MagicMock(),
            "json": MagicMock(),
            "md": MagicMock(),
            "misses": MagicMock(),
        }
        return gen

    def test_suggest_table_output(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            res = CliRunner().invoke(cli, ["candidates", "suggest", "--count", "1"])
        assert res.exit_code == 0, res.output
        assert "Pendulum" in res.output and "Slam" in res.output
        gen.suggest.assert_awaited_once()

    def test_suggest_json_output(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            res = CliRunner().invoke(cli, ["candidates", "suggest", "--format", "json"])
        assert res.exit_code == 0, res.output
        assert "Pendulum" in res.output and "NOMAD" in res.output

    def test_suggest_passes_min_score(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen) as build:
            CliRunner().invoke(cli, ["candidates", "suggest", "--min-score", "60"])
        assert build.call_args.kwargs["min_score"] == 60.0


class TestSingableCommand:
    def _fake_gen(self):
        song = SingableSong(
            artist="Pendulum",
            title="Slam",
            playcount=88,
            brands=["NOMAD", "WTF"],
            watch="https://youtu.be/abc123",
            version_count=2,
        )
        result = SingableResult(songs=[song], considered=500, matched=1)
        gen = MagicMock()
        gen.singable = AsyncMock(return_value=result)
        gen.write_singable_reports.return_value = {
            "csv": MagicMock(),
            "json": MagicMock(),
            "md": MagicMock(),
        }
        return gen

    def test_singable_table_output(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            res = CliRunner().invoke(cli, ["candidates", "singable", "--count", "10"])
        assert res.exit_code == 0, res.output
        assert "Pendulum" in res.output and "Slam" in res.output
        gen.singable.assert_awaited_once()

    def test_singable_json_output(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            res = CliRunner().invoke(cli, ["candidates", "singable", "--format", "json"])
        assert res.exit_code == 0, res.output
        assert "Pendulum" in res.output and "youtu.be/abc123" in res.output

    def test_singable_passes_options(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            CliRunner().invoke(cli, ["candidates", "singable", "--count", "7", "--min-plays", "3"])
        kwargs = gen.singable.await_args.kwargs
        assert kwargs["count"] == 7 and kwargs["min_plays"] == 3
