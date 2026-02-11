"""Job scheduling for nightly reviews."""

import logging
import os
import time as _time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.utils import parse_time_string

logger = logging.getLogger("lucidpulls.scheduler")

# Heartbeat file for health checks — written on start and after each job
_default_heartbeat = "/app/data/heartbeat" if Path("/app").is_dir() else "data/heartbeat"
HEARTBEAT_PATH = Path(os.environ.get("HEARTBEAT_PATH", _default_heartbeat)).resolve()


def _write_heartbeat() -> None:
    """Write current timestamp to heartbeat file for health checks."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(str(int(_time.time())))
    except OSError as e:
        logger.warning(f"Failed to write heartbeat: {e}")


def check_heartbeat(max_age_seconds: int = 3700) -> bool:
    """Check if the heartbeat file is recent enough.

    Args:
        max_age_seconds: Maximum age in seconds (default ~1 hour).

    Returns:
        True if heartbeat is recent.
    """
    try:
        if not HEARTBEAT_PATH.exists():
            return False
        ts = int(HEARTBEAT_PATH.read_text().strip())
        return (_time.time() - ts) < max_age_seconds
    except (OSError, ValueError):
        return False


class ReviewScheduler:
    """Schedules and manages review jobs."""

    def __init__(self, timezone: str = "America/New_York"):
        """Initialize scheduler.

        Args:
            timezone: Timezone for scheduling (IANA format).
        """
        self.timezone = pytz.timezone(timezone)
        self.scheduler = BlockingScheduler(timezone=self.timezone)
        self._review_job_id = "nightly_review"
        self._report_job_id = "morning_report"

    def schedule_review(
        self,
        start_time: str,
        review_func: Callable[[], None],
    ) -> None:
        """Schedule the nightly review job.

        Args:
            start_time: Time to start review (HH:MM format).
            review_func: Function to call for review.
        """
        hour, minute = parse_time_string(start_time)

        def _review_with_heartbeat() -> None:
            try:
                review_func()
            finally:
                _write_heartbeat()

        self.scheduler.add_job(
            _review_with_heartbeat,
            CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=self._review_job_id,
            replace_existing=True,
            name="Nightly Code Review",
            misfire_grace_time=3600,
            coalesce=True,
        )

        logger.info(f"Scheduled nightly review at {start_time} {self.timezone}")

    def schedule_report(
        self,
        delivery_time: str,
        report_func: Callable[[], None],
    ) -> None:
        """Schedule the morning report job.

        Args:
            delivery_time: Time to deliver report (HH:MM format).
            report_func: Function to call for report delivery.
        """
        hour, minute = parse_time_string(delivery_time)

        def _report_with_heartbeat() -> None:
            try:
                report_func()
            finally:
                _write_heartbeat()

        self.scheduler.add_job(
            _report_with_heartbeat,
            CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=self._report_job_id,
            replace_existing=True,
            name="Morning Report Delivery",
            misfire_grace_time=3600,
            coalesce=True,
        )

        logger.info(f"Scheduled morning report at {delivery_time} {self.timezone}")

    def start(self) -> None:
        """Start the scheduler (blocking)."""
        logger.info("Starting scheduler...")
        _write_heartbeat()
        self.scheduler.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=False)

    def get_next_run_time(self, job_id: str | None = None) -> datetime | None:
        """Get next scheduled run time.

        Args:
            job_id: Specific job ID, or None for review job.

        Returns:
            Next run datetime if scheduled.
        """
        job = self.scheduler.get_job(job_id or self._review_job_id)
        if job:
            return getattr(job, 'next_run_time', None)
        return None

class DeadlineEnforcer:
    """Enforces review deadline by tracking elapsed time.

    Call mark_review_started() at the beginning of each review cycle.
    Then is_past_deadline() checks whether the deadline time-of-day has
    been reached since the review started, correctly handling midnight crossover.
    """

    def __init__(self, deadline_time: str, timezone: str = "America/New_York"):
        """Initialize deadline enforcer.

        Args:
            deadline_time: Deadline time (HH:MM format).
            timezone: Timezone for deadline.
        """
        self.deadline_hour, self.deadline_minute = parse_time_string(deadline_time)
        self.timezone = pytz.timezone(timezone)
        self._review_started_at: datetime | None = None

    def mark_review_started(self) -> None:
        """Record that a review cycle has started. Call at the start of each run."""
        self._review_started_at = datetime.now(self.timezone)

    def _get_deadline_for_current_cycle(self) -> datetime:
        """Get the deadline datetime for the current review cycle.

        Uses the review start time as an anchor. The deadline is the first
        occurrence of deadline_hour:deadline_minute that is after the review
        started.
        """
        from datetime import timedelta

        anchor = self._review_started_at or datetime.now(self.timezone)

        deadline = anchor.replace(
            hour=self.deadline_hour,
            minute=self.deadline_minute,
            second=0,
            microsecond=0,
        )

        # If deadline time-of-day is at or before the review start time-of-day,
        # the deadline is tomorrow (e.g. start 11 PM, deadline 6 AM)
        if deadline <= anchor:
            deadline += timedelta(days=1)

        return deadline

    def is_past_deadline(self) -> bool:
        """Check if current time is past the deadline for the current review cycle.

        Returns:
            True if deadline has passed and we should stop processing.
        """
        now = datetime.now(self.timezone)
        deadline = self._get_deadline_for_current_cycle()
        return now >= deadline

    def time_remaining(self) -> int | None:
        """Get seconds remaining until the deadline.

        Returns:
            Seconds until deadline, or None if already past.
        """
        now = datetime.now(self.timezone)
        deadline = self._get_deadline_for_current_cycle()
        remaining = (deadline - now).total_seconds()
        return int(remaining) if remaining > 0 else None
