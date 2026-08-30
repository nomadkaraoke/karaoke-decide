"""CLI tests for the candidates command group (no network)."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from karaoke_decide.candidates.generator import Candidate, SuggestResult
from karaoke_decide.candidates.lyrics import analyze
from karaoke_decide.cli.main import cli


class TestRejectCommands:
    def test_reject_and_review(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANDIDATES_DIR", str(tmp_path))
        runner = CliRunner()

        res = runner.invoke(
            cli,
            ["candidates", "reject", "Pendulum", "Slam", "--reason", "too repetitive"],
        )
        assert res.exit_code == 0, res.output
        assert "Rejected" in res.output

        res = runner.invoke(cli, ["candidates", "review-rejects"])
        assert res.exit_code == 0
        assert "Pendulum" in res.output
        assert "too repetitive" in res.output

    def test_review_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANDIDATES_DIR", str(tmp_path))
        res = CliRunner().invoke(cli, ["candidates", "review-rejects"])
        assert res.exit_code == 0
        assert "No rejects" in res.output

    def test_help_lists_subcommands(self):
        res = CliRunner().invoke(cli, ["candidates", "--help"])
        assert res.exit_code == 0
        for cmd in ("suggest", "calibrate", "reject", "review-rejects"):
            assert cmd in res.output


class TestSuggestCommand:
    def _fake_gen(self):
        cand = Candidate(
            artist="Pendulum",
            title="Slam",
            playcount=88,
            is_electronic=True,
            stats=analyze("line one\nline two\nline three"),
            flac={"provider": "RED", "seeders": 388, "format": "FLAC"},
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
        assert "Pendulum" in res.output
        assert "Slam" in res.output
        gen.suggest.assert_awaited_once()

    def test_suggest_json_output(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen):
            res = CliRunner().invoke(cli, ["candidates", "suggest", "--format", "json"])
        assert res.exit_code == 0, res.output
        assert "Pendulum" in res.output
        assert "NOMAD" in res.output

    def test_suggest_passes_thresholds(self):
        gen = self._fake_gen()
        with patch("karaoke_decide.cli.candidates._build_generator", return_value=gen) as build:
            CliRunner().invoke(
                cli,
                ["candidates", "suggest", "--electronic-min-unique-lines", "20"],
            )
        thresholds = build.call_args.kwargs["thresholds"]
        assert thresholds.electronic_min_unique_lines == 20
