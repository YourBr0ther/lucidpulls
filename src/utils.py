"""Shared utilities for LucidPulls."""

import logging
import re
import time
from functools import wraps
from typing import Callable, TypeVar, Any

T = TypeVar("T")

logger = logging.getLogger("lucidpulls.utils")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts.
        delay: Initial delay between retries in seconds.
        backoff: Multiplier for delay after each attempt.
        exceptions: Tuple of exception types to catch and retry.

    Returns:
        Decorated function.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt}/{max_attempts}): {e}"
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff

            raise last_exception or RuntimeError("Retry failed without exception")
        return wrapper
    return decorator


def sanitize_branch_name(name: str) -> str:
    """Sanitize a string for use in git branch names.

    Args:
        name: Raw string to sanitize.

    Returns:
        Sanitized branch name component.
    """
    # Replace path separators and spaces with dashes
    name = name.replace("/", "-").replace("\\", "-").replace(" ", "-")
    # Remove invalid git branch characters (keep only alphanumeric, dots, dashes, underscores)
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    # Remove consecutive dashes
    name = re.sub(r"-+", "-", name)
    # Trim dashes from ends
    name = name.strip("-")
    # Limit length
    return name[:50] if len(name) > 50 else name


def parse_time_string(time_str: str) -> tuple[int, int]:
    """Parse time string into hour and minute.

    Args:
        time_str: Time in HH:MM format.

    Returns:
        Tuple of (hour, minute).

    Raises:
        ValueError: If format is invalid.
    """
    if not time_str or ":" not in time_str:
        raise ValueError(f"Invalid time format '{time_str}', expected HH:MM")

    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{time_str}', expected HH:MM")

    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time format '{time_str}', expected numeric HH:MM")

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {time_str}")

    return hour, minute
