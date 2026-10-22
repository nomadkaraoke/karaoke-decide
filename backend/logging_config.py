"""stdout logging for Cloud Run, shared by ``backend.main`` and ``backend.serve``."""

import logging
import sys


def configure_logging() -> None:
    """INFO to stdout; idempotent (``basicConfig`` is a no-op once the root has handlers)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        stream=sys.stdout,
    )
    # httpx/httpcore log every request URL at INFO, which leaks query-string API
    # keys (e.g. Last.fm api_key) into Cloud Logging.
    for noisy_logger in ("httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
