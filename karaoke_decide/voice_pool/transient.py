"""Errors that mean "try again later", shared by the voice-pool jobs (fetch's Cloud Run calls,
analyse's GCS / BigQuery reads). A transient error is a WARNING and the work stays where it was
for the next run; it never fails a song or becomes the run's ERROR."""

from __future__ import annotations

import requests
from google.api_core.exceptions import DeadlineExceeded, InternalServerError, ServiceUnavailable, TooManyRequests
from google.auth.exceptions import TransportError

__all__ = ["TRANSIENT_ERRORS"]

TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    ServiceUnavailable,
    DeadlineExceeded,
    TooManyRequests,
    InternalServerError,
    ConnectionError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    TransportError,
)
