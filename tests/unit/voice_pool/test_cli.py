"""``python -m karaoke_decide.voice_pool``: argument handling and the dry-run report (no GCP, no network)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.config import get_backend_settings
from karaoke_decide.voice_pool import cli


@pytest.fixture
def no_bq(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BIGQUERY_DISABLED", "1")
    # configure_logging(force=True) would replace pytest's capture handler.
    monkeypatch.setattr("karaoke_decide.etl.refresh_common.configure_logging", lambda: None)
    get_backend_settings.cache_clear()
    yield
    get_backend_settings.cache_clear()


def test_pending_commands_exit_2(capsys):
    assert cli.main(["fetch"]) == 2
    assert "Task A4" in capsys.readouterr().err


def test_select_dry_run_prints_report(no_bq, catalog_db: Path, availability_db: Path, capsys):
    argv = ["select", "--n", "20", "--dry-run", "--no-probe", "--run", "t1"]
    assert cli.main([*argv, "--catalog", str(catalog_db), "--availability", str(availability_db)]) == 0
    out = capsys.readouterr().out
    assert "voice pool selection t1 (dry run)" in out
    assert "picked 20/20" in out and "enqueued 0" in out
    assert "gender" in out and "decade" in out


def test_select_needs_bigquery_to_write(no_bq, capsys):
    assert cli.main(["select", "--n", "5"]) == 2
    assert "only --dry-run" in capsys.readouterr().err


def test_select_failure_logs_one_error(no_bq, tmp_path: Path, caplog):
    missing = tmp_path / "nope.db"
    rc = cli.main(["select", "--dry-run", "--catalog", str(missing), "--availability", str(missing)])
    assert rc == 1
    assert [r.levelno for r in caplog.records if r.levelno >= logging.ERROR] == [logging.ERROR]


def test_catalog_and_availability_go_together(no_bq, catalog_db: Path):
    with pytest.raises(SystemExit):
        cli.main(["select", "--dry-run", "--catalog", str(catalog_db)])


def test_format_report_marks_below_minimum():
    summary = {
        "run": "r", "dry_run": False, "picked": 2, "target": 1000, "candidates": 5, "below_minimum": True,
        "probed": 3, "non_embeddable": 1, "enqueued": 2, "per_dimension": {"duet": {"solo": [900, 2], "duet": [100, 0]}},
        "empty_values": ["duet=duet"], "coverage_uri": "gs://b/voice/selection/r/coverage.json",
    }  # fmt: skip
    text = cli.format_report(summary)
    assert "BELOW MINIMUM" in text and "solo 2/900" in text and "empty: duet=duet" in text
    assert "gs://b/voice/selection/r/coverage.json" in text
