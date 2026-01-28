"""Tests for notification channels."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import httpx

from src.notifications.base import NotificationResult, PRSummary, ReviewReport
from src.notifications.discord import DiscordNotifier
from src.notifications.teams import TeamsNotifier
from src.notifications import get_notifier


class TestReviewReport:
    """Tests for ReviewReport."""

    def test_duration_str_minutes(self):
        """Test duration string for minutes only."""
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=3,
            prs_created=2,
            prs=[],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 2, 45),
        )
        assert report.duration_str == "45m"

    def test_duration_str_hours_and_minutes(self):
        """Test duration string for hours and minutes."""
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=3,
            prs_created=2,
            prs=[],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 4, 30),
        )
        assert report.duration_str == "2h 30m"


class TestDiscordNotifier:
    """Tests for DiscordNotifier."""

    def test_init(self):
        """Test initialization."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert notifier.webhook_url == "https://discord.com/api/webhooks/123/abc"
        assert notifier.channel_name == "Discord"

    def test_is_configured_valid(self):
        """Test is_configured with valid URL."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert notifier.is_configured() is True

    def test_is_configured_invalid(self):
        """Test is_configured with invalid URL."""
        notifier = DiscordNotifier(webhook_url="")
        assert notifier.is_configured() is False

    def test_is_configured_wrong_domain(self):
        """Test is_configured with wrong domain."""
        notifier = DiscordNotifier(webhook_url="https://example.com/webhook")
        assert notifier.is_configured() is False

    @patch.object(httpx.Client, "post")
    def test_send_report_success(self, mock_post):
        """Test successful report sending."""
        mock_post.return_value = Mock(status_code=204)

        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=2,
            prs_created=1,
            prs=[
                PRSummary(
                    repo_name="owner/repo",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo/pull/42",
                    pr_title="Fix bug",
                    success=True,
                )
            ],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 3, 0),
        )

        result = notifier.send_report(report)
        assert result.success is True

    def test_send_report_not_configured(self):
        """Test sending when not configured."""
        notifier = DiscordNotifier(webhook_url="")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=0,
            prs_created=0,
            prs=[],
            start_time=datetime.now(),
            end_time=datetime.now(),
        )

        result = notifier.send_report(report)
        assert result.success is False
        assert "not configured" in result.error

    def test_build_discord_payload(self):
        """Test Discord payload building."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = ReviewReport(
            date=datetime(2024, 1, 15),
            repos_reviewed=2,
            prs_created=1,
            prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo1/pull/42",
                    pr_title="Fix bug",
                    success=True,
                ),
                PRSummary(
                    repo_name="owner/repo2",
                    pr_number=None,
                    pr_url=None,
                    pr_title=None,
                    success=False,
                    error="No fixes found",
                ),
            ],
            start_time=datetime(2024, 1, 15, 2, 0),
            end_time=datetime(2024, 1, 15, 3, 0),
        )

        payload = notifier._build_discord_payload(report)

        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "2024-01-15" in embed["title"]
        assert "2 repositories reviewed" in embed["description"]
        assert len(embed["fields"]) == 2


class TestTeamsNotifier:
    """Tests for TeamsNotifier."""

    def test_init(self):
        """Test initialization."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        assert notifier.webhook_url == "https://outlook.office.com/webhook/123"
        assert notifier.channel_name == "Microsoft Teams"

    def test_is_configured_valid(self):
        """Test is_configured with valid URL."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        assert notifier.is_configured() is True

    def test_is_configured_invalid(self):
        """Test is_configured with invalid URL."""
        notifier = TeamsNotifier(webhook_url="")
        assert notifier.is_configured() is False

    @patch.object(httpx.Client, "post")
    def test_send_report_success(self, mock_post):
        """Test successful report sending."""
        mock_post.return_value = Mock(status_code=200)

        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=1,
            prs_created=1,
            prs=[
                PRSummary(
                    repo_name="owner/repo",
                    pr_number=10,
                    pr_url="https://github.com/owner/repo/pull/10",
                    pr_title="Fix",
                    success=True,
                )
            ],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 3, 0),
        )

        result = notifier.send_report(report)
        assert result.success is True

    def test_build_teams_payload(self):
        """Test Teams payload building."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = ReviewReport(
            date=datetime(2024, 1, 15),
            repos_reviewed=1,
            prs_created=1,
            prs=[
                PRSummary(
                    repo_name="owner/repo",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo/pull/42",
                    pr_title="Fix bug",
                    success=True,
                )
            ],
            start_time=datetime(2024, 1, 15, 2, 0),
            end_time=datetime(2024, 1, 15, 3, 0),
        )

        payload = notifier._build_teams_payload(report)

        assert payload["type"] == "message"
        assert "attachments" in payload
        card = payload["attachments"][0]["content"]
        assert card["type"] == "AdaptiveCard"


class TestGetNotifier:
    """Tests for get_notifier factory function."""

    def test_get_discord(self):
        """Test getting Discord notifier."""
        notifier = get_notifier("discord", {"webhook_url": "https://discord.com/api/webhooks/123"})
        assert isinstance(notifier, DiscordNotifier)

    def test_get_teams(self):
        """Test getting Teams notifier."""
        notifier = get_notifier("teams", {"webhook_url": "https://outlook.office.com/webhook/123"})
        assert isinstance(notifier, TeamsNotifier)

    def test_get_invalid_channel(self):
        """Test getting invalid channel raises error."""
        with pytest.raises(ValueError, match="Unsupported notification channel"):
            get_notifier("invalid", {})
