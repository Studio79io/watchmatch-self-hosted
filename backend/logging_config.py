import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Small dependency-free JSON formatter for container logs."""

    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("event", "room_code", "sync_trigger", "sync_run_id",
                    "item_count", "added_count", "updated_count", "removed_count",
                    "method", "path", "status_code", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
