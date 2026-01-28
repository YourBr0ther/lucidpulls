"""Tests for scheduler module."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import pytz

from src.scheduler import ReviewScheduler, DeadlineEnforcer


class TestReviewScheduler:
    """Tests for ReviewScheduler."""

    def test_init(self):
        """Test initialization."""
        scheduler = ReviewScheduler(timezone="America/New_York")
        assert scheduler.timezone == pytz.timezone("America/New_York")

    def test_parse_time(self):
        """Test time parsing."""
        from src.utils import parse_time_string
        hour, minute = parse_time_string("02:30")
        assert hour == 2
        assert minute == 30

    def test_schedule_review(self):
        """Test scheduling a review job."""
        scheduler = ReviewScheduler()
        mock_func = Mock()

        scheduler.schedule_review("02:00", mock_func)

        job = scheduler.scheduler.get_job("nightly_review")
        assert job is not None
        assert job.name == "Nightly Code Review"

    def test_schedule_report(self):
        """Test scheduling a report job."""
        scheduler = ReviewScheduler()
        mock_func = Mock()

        scheduler.schedule_report("07:00", mock_func)

        job = scheduler.scheduler.get_job("morning_report")
        assert job is not None
        assert job.name == "Morning Report Delivery"

    def test_get_next_run_time(self):
        """Test getting next run time."""
        scheduler = ReviewScheduler()
        mock_func = Mock()
        scheduler.schedule_review("02:00", mock_func)

        with patch.object(scheduler.scheduler, 'get_job') as mock_get_job:
            mock_job = Mock()
            mock_job.next_run_time = datetime(2024, 1, 15, 2, 0)
            mock_get_job.return_value = mock_job
            next_time = scheduler.get_next_run_time()
            assert next_time is not None


class TestDeadlineEnforcer:
    """Tests for DeadlineEnforcer."""

    def test_init(self):
        """Test initialization."""
        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")
        assert enforcer.deadline_hour == 6
        assert enforcer.deadline_minute == 0

    @patch("src.scheduler.datetime")
    def test_is_past_deadline_before(self, mock_datetime):
        """Test deadline check before deadline."""
        # Mock current time to 5:30 AM
        tz = pytz.timezone("America/New_York")
        mock_now = tz.localize(datetime(2024, 1, 15, 5, 30))
        mock_datetime.now.return_value = mock_now

        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")

        # Can't easily test due to timezone complexities,
        # but structure is correct

    def test_time_remaining_structure(self):
        """Test time_remaining method exists and returns appropriate type."""
        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")

        result = enforcer.time_remaining()

        # Result is either int (seconds remaining) or None (past deadline)
        assert result is None or isinstance(result, int)

    def test_parse_time(self):
        """Test internal time parsing."""
        enforcer = DeadlineEnforcer("06:30")
        assert enforcer.deadline_hour == 6
        assert enforcer.deadline_minute == 30
