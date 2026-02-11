"""Tests for scheduler module."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytz

from src.scheduler import DeadlineEnforcer, ReviewScheduler


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

    def test_review_job_has_misfire_handling(self):
        """Test that review job is configured with misfire grace and coalesce."""
        scheduler = ReviewScheduler()
        scheduler.schedule_review("02:00", Mock())

        job = scheduler.scheduler.get_job("nightly_review")
        assert job.misfire_grace_time == 3600
        assert job.coalesce is True

    def test_report_job_has_misfire_handling(self):
        """Test that report job is configured with misfire grace and coalesce."""
        scheduler = ReviewScheduler()
        scheduler.schedule_report("07:00", Mock())

        job = scheduler.scheduler.get_job("morning_report")
        assert job.misfire_grace_time == 3600
        assert job.coalesce is True

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
        """Test deadline check returns False before deadline."""
        tz = pytz.timezone("America/New_York")
        mock_datetime.now.return_value = tz.localize(datetime(2024, 1, 15, 5, 30))

        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")
        enforcer._review_started_at = tz.localize(datetime(2024, 1, 15, 5, 0))

        assert enforcer.is_past_deadline() is False

    @patch("src.scheduler.datetime")
    def test_is_past_deadline_after(self, mock_datetime):
        """Test deadline check returns True after deadline."""
        tz = pytz.timezone("America/New_York")
        mock_datetime.now.return_value = tz.localize(datetime(2024, 1, 15, 6, 30))

        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")
        enforcer._review_started_at = tz.localize(datetime(2024, 1, 15, 5, 0))

        assert enforcer.is_past_deadline() is True

    @patch("src.scheduler.datetime")
    def test_time_remaining_before_deadline(self, mock_datetime):
        """Test time_remaining returns correct seconds before deadline."""
        tz = pytz.timezone("America/New_York")
        mock_datetime.now.return_value = tz.localize(datetime(2024, 1, 15, 5, 30))

        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")
        enforcer._review_started_at = tz.localize(datetime(2024, 1, 15, 5, 0))

        result = enforcer.time_remaining()
        assert result == 1800  # 30 minutes = 1800 seconds

    @patch("src.scheduler.datetime")
    def test_time_remaining_past_deadline(self, mock_datetime):
        """Test time_remaining returns None after deadline."""
        tz = pytz.timezone("America/New_York")
        mock_datetime.now.return_value = tz.localize(datetime(2024, 1, 15, 6, 30))

        enforcer = DeadlineEnforcer("06:00", timezone="America/New_York")
        enforcer._review_started_at = tz.localize(datetime(2024, 1, 15, 5, 0))

        assert enforcer.time_remaining() is None

    def test_parse_time(self):
        """Test internal time parsing."""
        enforcer = DeadlineEnforcer("06:30")
        assert enforcer.deadline_hour == 6
        assert enforcer.deadline_minute == 30
