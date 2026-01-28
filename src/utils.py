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

            raise last_exception
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
