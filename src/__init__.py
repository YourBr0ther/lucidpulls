"""LucidPulls - Code review for bugs while you sleep."""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

# Context variable for correlating logs within a review run
current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_run_id", default="-"
)


class RunIDFilter(logging.Filter):
    """Injects run_id from contextvars into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = current_run_id.get("-")
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging in production."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": getattr(record, "run_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def setup_logging(level: Optional[str] = None, log_format: str = "text") -> logging.Logger:
    """Configure logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to INFO if not specified.
        log_format: Output format - 'text' for human-readable, 'json' for structured.

    Returns:
        Configured logger instance.
    """
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)

    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | run=%(run_id)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RunIDFilter())

    logger = logging.getLogger("lucidpulls")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(handler)

    # Prevent duplicate logs if setup is called multiple times
    logger.propagate = False

    return logger


__version__ = "0.1.0"
__all__ = ["setup_logging", "current_run_id", "RunIDFilter", "__version__"]
