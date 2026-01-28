"""LucidPulls - Code review for bugs while you sleep."""

import logging
import sys
from typing import Optional


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Configure logging for the application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to INFO if not specified.

    Returns:
        Configured logger instance.
    """
    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logger = logging.getLogger("lucidpulls")
    logger.setLevel(log_level)
    logger.addHandler(handler)

    # Prevent duplicate logs if setup is called multiple times
    logger.propagate = False

    return logger


__version__ = "0.1.0"
__all__ = ["setup_logging", "__version__"]
