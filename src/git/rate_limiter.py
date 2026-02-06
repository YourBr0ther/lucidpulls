"""Shared GitHub API rate limiter."""

import logging
import threading
import time

from github import Github

logger = logging.getLogger("lucidpulls.git.rate_limiter")


class RateLimitExhausted(Exception):
    """Raised when GitHub API rate limit is exhausted."""

    def __init__(self, wait_seconds: float):
        self.wait_seconds = wait_seconds
        super().__init__(f"GitHub rate limit exhausted, reset in {wait_seconds:.0f}s")


class GitHubRateLimiter:
    """Unified rate limiter for GitHub API calls.

    Enforces a minimum delay between calls and checks remaining quota.
    Raises RateLimitExhausted instead of sleeping when quota is empty,
    letting the caller decide how to handle it.
    """

    def __init__(
        self,
        github: Github,
        min_delay: float = 0.5,
        shutdown_event: threading.Event | None = None,
    ):
        """Initialize rate limiter.

        Args:
            github: Shared Github client instance.
            min_delay: Minimum delay between API calls in seconds.
            shutdown_event: Optional event to interrupt waits on shutdown.
        """
        self.github = github
        self._min_delay = min_delay
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._shutdown_event = shutdown_event or threading.Event()

    def throttle(self) -> None:
        """Enforce minimum delay between calls and check quota.

        Raises:
            RateLimitExhausted: If API quota is exhausted.
        """
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self._min_delay:
                remaining_delay = self._min_delay - elapsed
                # Use event wait so shutdown can interrupt
                self._shutdown_event.wait(timeout=remaining_delay)
            self._last_call = time.time()

        self._check_quota()

    def _check_quota(self) -> None:
        """Check remaining GitHub API quota.

        Raises:
            RateLimitExhausted: If quota is exhausted (remaining == 0).
        """
        try:
            rate_limit = self.github.get_rate_limit()
            core = rate_limit.rate

            if core.remaining < 10:
                reset_time = core.reset.timestamp()
                wait_seconds = max(reset_time - time.time(), 0) + 5

                if core.remaining == 0:
                    logger.warning(
                        f"GitHub rate limit exhausted. Reset in {wait_seconds:.0f}s."
                    )
                    raise RateLimitExhausted(wait_seconds)
                else:
                    logger.info(
                        f"GitHub rate limit low ({core.remaining} remaining). "
                        f"Reset in {wait_seconds:.0f}s."
                    )
        except RateLimitExhausted:
            raise
        except Exception as e:
            logger.debug(f"Could not check rate limit: {e}")

    def wait_for_reset(self, wait_seconds: float) -> bool:
        """Wait for rate limit reset, interruptible by shutdown.

        Args:
            wait_seconds: Seconds to wait.

        Returns:
            True if wait completed normally, False if interrupted by shutdown.
        """
        logger.info(f"Waiting {wait_seconds:.0f}s for rate limit reset...")
        interrupted = self._shutdown_event.wait(timeout=wait_seconds)
        return not interrupted
