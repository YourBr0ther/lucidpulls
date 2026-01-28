"""Job scheduling for nightly reviews."""

import logging
from datetime import datetime
from typing import Callable, Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

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
        hour, minute = self._parse_time(start_time)

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
        hour, minute = self._parse_time(delivery_time)

        self.scheduler.add_job(
            report_func,
            CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=self._report_job_id,
            replace_existing=True,
            name="Morning Report Delivery",
        )

        logger.info(f"Scheduled morning report at {delivery_time} {self.timezone}")

    def _parse_time(self, time_str: str) -> tuple[int, int]:
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
            return job.next_run_time
        return None

    def run_now(self, job_id: Optional[str] = None) -> None:
        """Trigger a job to run immediately.

        Args:
            job_id: Specific job ID, or None for review job.
        """
        job = self.scheduler.get_job(job_id or self._review_job_id)
        if job:
            logger.info(f"Triggering immediate run: {job.name}")
            job.modify(next_run_time=datetime.now(self.timezone))


class DeadlineEnforcer:
    """Enforces review deadline by tracking elapsed time."""

    def __init__(self, deadline_time: str, timezone: str = "America/New_York"):
        """Initialize deadline enforcer.

        Args:
            deadline_time: Deadline time (HH:MM format).
            timezone: Timezone for deadline.
        """
        self.deadline_hour, self.deadline_minute = self._parse_time(deadline_time)
        self.timezone = pytz.timezone(timezone)

    def _parse_time(self, time_str: str) -> tuple[int, int]:
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

    def is_past_deadline(self) -> bool:
        """Check if current time is past the deadline.

        Returns:
            True if deadline has passed.
        """
        now = datetime.now(self.timezone)
        deadline = now.replace(
            hour=self.deadline_hour,
            minute=self.deadline_minute,
            second=0,
            microsecond=0,
        )

        # If deadline time is earlier than now but we're checking for "tonight's" deadline,
        # it means the deadline hasn't passed yet (it's tomorrow)
        # Example: It's 11 PM, deadline is 2 AM -> deadline is tomorrow 2 AM
        if deadline <= now and self.deadline_hour < 12 and now.hour >= 18:
            # Deadline is meant for tomorrow
            return False

        # If deadline is in the past and it's morning, we're past deadline
        if deadline <= now and now.hour < 12:
            return True

        # If deadline is in the future, we're not past it
        if deadline > now:
            return False

        return now >= deadline

    def time_remaining(self) -> Optional[int]:
        """Get seconds remaining until deadline.

        Returns:
            Seconds until deadline, or None if past.
        """
        now = datetime.now(self.timezone)
        deadline = now.replace(
            hour=self.deadline_hour,
            minute=self.deadline_minute,
            second=0,
            microsecond=0,
        )

        # Handle wraparound for early morning deadlines
        if self.deadline_hour < 12 and now.hour >= 12:
            # Deadline is tomorrow
            from datetime import timedelta
            deadline = deadline + timedelta(days=1)

        remaining = (deadline - now).total_seconds()
        return int(remaining) if remaining > 0 else None
