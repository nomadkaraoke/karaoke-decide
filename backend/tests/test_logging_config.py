"""Cloud Run Job logging emits severity so Cloud Logging classifies ERROR lines."""

import json
import logging
from collections.abc import Iterator

import pytest

from backend.logging_config import configure_job_logging


@pytest.fixture
def bare_root() -> Iterator[logging.Logger]:
    """Restores the root logger; tests strip its handlers (pytest adds its own per phase)."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def test_job_logging_emits_json_severity_on_cloud_run(
    bare_root: logging.Logger, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUD_RUN_JOB", "lb-poll")
    bare_root.handlers = []  # a fresh job process: no handlers yet
    configure_job_logging()

    logging.getLogger("lb_poll").error("poll failed for fixture-user")
    logging.getLogger("lb_poll").info("summary")

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(e["severity"], e["message"], e["logger"]) for e in lines] == [
        ("ERROR", "poll failed for fixture-user", "lb_poll"),
        ("INFO", "summary", "lb_poll"),
    ]


def test_job_logging_is_plain_text_locally(
    bare_root: logging.Logger, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    bare_root.handlers = []  # a fresh job process: no handlers yet
    configure_job_logging()

    logging.getLogger("lb_poll").warning("hello")

    out = capsys.readouterr().out
    assert "WARNING lb_poll hello" in out
    assert logging.getLogger("httpx").level == logging.WARNING
