"""Structured search logging (spec §5.6): one Cloud Logging line per event.

Records go to ``logging.getLogger("search_events")`` with their fields in
``extra={"json_fields": {...}}``; ``JsonLineFormatter`` turns every record into one
JSON line on stdout, which Cloud Run ships to Cloud Logging as ``jsonPayload``.
Fields follow kjbox ``search_log.py`` plus the client search-session id ``sid``
and the anonymous device id. Logging never breaks search: failures are swallowed.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

EVENTS_LOGGER = "search_events"

_logger = logging.getLogger(EVENTS_LOGGER)


def log_event(event_type: str, *, sid: str | None, device: str | None, **fields: Any) -> None:
    """One INFO line ``{"type", "sid", "device", **fields}``; never raises."""
    try:
        payload = {"type": event_type, "sid": sid, "device": device, **fields}
        _logger.info(event_type, extra={"json_fields": payload})
    except Exception:  # noqa: BLE001 — logging must never break search (A102)
        pass


class JsonLineFormatter(logging.Formatter):
    """``{"severity", "message", "logger", **json_fields}`` as one JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        fields = getattr(record, "json_fields", None)
        if isinstance(fields, dict):
            out.update(fields)
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


_HANDLER_FLAG = "_search_json_handler"


def configure_logging() -> None:
    """Send INFO+ to stdout as JSON lines (idempotent)."""
    root = logging.getLogger()
    if any(getattr(h, _HANDLER_FLAG, False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    setattr(handler, _HANDLER_FLAG, True)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # httpx logs full request URLs at INFO (backend/main.py does the same).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
