"""Cloud Run Job logging emits severity so Cloud Logging classifies ERROR lines."""

import json
import logging
from collections.abc import Iterator

import pytest

from backend.logging_config import configure_job_logging, configure_logging


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


def test_service_logging_keeps_traceback_inside_one_warning_record_on_cloud_run(
    bare_root: logging.Logger, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A WARNING with exc_info is ONE JSON line at WARNING, so Cloud Logging never splits the
    traceback into separate entries that the error monitor reads as ERRORs."""
    monkeypatch.setenv("K_SERVICE", "karaoke-decide")
    bare_root.handlers = []
    configure_logging()

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("backend.api.routes.services").warning("callback failed", exc_info=True)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["severity"] == "WARNING"
    assert entry["logger"] == "backend.api.routes.services"
    assert entry["message"].startswith("callback failed\nTraceback")
    assert "RuntimeError: boom" in entry["message"]


def test_service_logging_is_plain_text_locally(
    bare_root: logging.Logger, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    bare_root.handlers = []
    configure_logging()

    logging.getLogger("backend.main").info("hello")

    assert "INFO:backend.main:hello" in capsys.readouterr().out
    assert logging.getLogger("httpcore").level == logging.WARNING
