"""Job scheduling for nightly reviews."""

import logging
from datetime import datetime
from typing import Callable, Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from src.utils import parse_time_string

logger = logging.getLogger("lucidpulls.scheduler")


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

        self.scheduler.add_job(
            review_func,
            CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=self._review_job_id,
            replace_existing=True,
            name="Nightly Code Review",
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

        self.scheduler.add_job(
            report_func,
            CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=self._report_job_id,
            replace_existing=True,
            name="Morning Report Delivery",
        )

        logger.info(f"Scheduled morning report at {delivery_time} {self.timezone}")

    def start(self) -> None:
        """Start the scheduler (blocking)."""
        logger.info("Starting scheduler...")
        self.scheduler.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=False)

    def get_next_run_time(self, job_id: Optional[str] = None) -> Optional[datetime]:
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
        self._review_started_at: Optional[datetime] = None

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

    def time_remaining(self) -> Optional[int]:
        """Get seconds remaining until the deadline.

        Returns:
            Seconds until deadline, or None if already past.
        """
        now = datetime.now(self.timezone)
        deadline = self._get_deadline_for_current_cycle()
        remaining = (deadline - now).total_seconds()
        return int(remaining) if remaining > 0 else None
