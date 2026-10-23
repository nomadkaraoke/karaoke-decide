"""stdout logging for Cloud Run, shared by ``backend.main``, ``backend.serve`` and the Cloud Run Jobs."""

import logging
import os
import sys


def configure_logging() -> None:
    """INFO to stdout; idempotent (``basicConfig`` is a no-op once the root has handlers)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        stream=sys.stdout,
    )
    _quiet_noisy_loggers()


def configure_job_logging() -> None:
    """Logging for a Cloud Run Job (``backend.jobs.*``); idempotent like ``configure_logging``.

    On Cloud Run Jobs (``CLOUD_RUN_JOB`` set) each record is one JSON line with ``severity``,
    the same format as the lb-refresh / mb-refresh jobs, so Cloud Logging and the error monitor
    classify ERROR lines. Plain stdout lines from a job carry no severity at all.
    """
    from karaoke_decide.etl.refresh_common import CloudLoggingFormatter

    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("CLOUD_RUN_JOB"):
        handler.setFormatter(CloudLoggingFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    _quiet_noisy_loggers()


def _quiet_noisy_loggers() -> None:
    # httpx/httpcore log every request URL at INFO, which leaks query-string API
    # keys (e.g. Last.fm api_key) into Cloud Logging.
    for noisy_logger in ("httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
