"""Cloud Logging's structured stdout format: one JSON object per record (stdlib only, cheap to import)."""

import json
import logging
from typing import Any


class CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line so Cloud Logging picks up severity."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        entry.update(getattr(record, "json_fields", None) or {})  # structured jsonPayload fields
        if record.exc_info:
            entry["message"] += "\n" + self.formatException(record.exc_info)
        return json.dumps(entry)
