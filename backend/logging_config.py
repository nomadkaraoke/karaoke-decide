"""stdout logging for Cloud Run, shared by ``backend.main``, ``backend.serve`` and the Cloud Run Jobs."""

import logging
import os
import sys

from karaoke_decide.core.cloud_logging import CloudLoggingFormatter


def configure_logging() -> None:
    """INFO to stdout for the API service; idempotent (``basicConfig`` is a no-op once the root has handlers).

    On Cloud Run (``K_SERVICE`` set) each record is one JSON line with ``severity``. Plain text
    would make Cloud Logging store every line of a traceback as its own entry with no severity of
    its own, and the error monitor pages on those, so a WARNING with ``exc_info`` used to alert.
    The JSON record keeps the traceback inside the record at that record's severity.
    """
    _configure_root(json_logs=bool(os.environ.get("K_SERVICE")), plain_format="%(levelname)s:%(name)s:%(message)s")


def configure_job_logging() -> None:
    """Logging for a Cloud Run Job (``backend.jobs.*``); idempotent like ``configure_logging``.

    On Cloud Run Jobs (``CLOUD_RUN_JOB`` set) each record is one JSON line with ``severity``,
    the same format as the lb-refresh / mb-refresh jobs, so Cloud Logging and the error monitor
    classify ERROR lines. Plain stdout lines from a job carry no severity at all.
    """
    _configure_root(
        json_logs=bool(os.environ.get("CLOUD_RUN_JOB")),
        plain_format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _configure_root(*, json_logs: bool, plain_format: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingFormatter() if json_logs else logging.Formatter(plain_format))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    _quiet_noisy_loggers()


def _quiet_noisy_loggers() -> None:
    # httpx/httpcore log every request URL at INFO, which leaks query-string API
    # keys (e.g. Last.fm api_key) into Cloud Logging.
    for noisy_logger in ("httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
