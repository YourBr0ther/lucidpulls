"""Tests for notification channels."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import httpx

from src.notifications.base import BaseNotifier, NotificationResult, PRSummary, ReviewReport
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


def _make_report(successful_prs=None, skipped_prs=None):
    """Helper to build a ReviewReport with successful and skipped PRs."""
    prs = []
    if successful_prs:
        prs.extend(successful_prs)
    if skipped_prs:
        prs.extend(skipped_prs)
    total_success = len(successful_prs) if successful_prs else 0
    total_repos = len(prs)
    return ReviewReport(
        date=datetime(2024, 1, 15),
        repos_reviewed=total_repos,
        prs_created=total_success,
        prs=prs,
        start_time=datetime(2024, 1, 15, 2, 0),
        end_time=datetime(2024, 1, 15, 3, 0),
    )


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

    def test_build_discord_payload_structure(self):
        """Test Discord payload has correct structure with collapse."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo1/pull/42",
                    pr_title="Fix bug",
                    success=True,
                    bug_description="Null pointer in handler",
                ),
            ],
            skipped_prs=[
                PRSummary(repo_name="owner/repo2", pr_number=None, pr_url=None,
                          pr_title=None, success=False, error="No fixes found"),
                PRSummary(repo_name="owner/repo3", pr_number=None, pr_url=None,
                          pr_title=None, success=False, error="No fixes found"),
            ],
        )

        payload = notifier._build_discord_payload(report)

        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "2024-01-15" in embed["title"]
        assert "3 repositories reviewed" in embed["description"]
        # 1 successful PR field + 1 collapsed skipped field = 2
        assert len(embed["fields"]) == 2
        # First field: successful PR with bug description blockquote
        assert ":white_check_mark:" in embed["fields"][0]["name"]
        assert "> Null pointer in handler" in embed["fields"][0]["value"]
        # Second field: collapsed skipped repos
        assert ":fast_forward:" in embed["fields"][1]["name"]
        assert "2 repos reviewed with no actionable issues found" in embed["fields"][1]["value"]

    def test_build_discord_payload_truncates_bug_description(self):
        """Test that bug descriptions longer than 120 chars are truncated."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        long_desc = "A" * 200
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix",
                    success=True,
                    bug_description=long_desc,
                ),
            ],
        )

        payload = notifier._build_discord_payload(report)
        value = payload["embeds"][0]["fields"][0]["value"]
        # Extract the blockquote line
        blockquote = [l for l in value.split("\n") if l.startswith(">")][0]
        # "> " prefix + 120 chars max (119 chars + ellipsis)
        assert len(blockquote) <= 2 + 120  # "> " + truncated text
        assert blockquote.endswith("\u2026")

    def test_build_discord_payload_no_skipped(self):
        """Test payload when all repos have successful PRs (no skipped section)."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                ),
            ],
        )

        payload = notifier._build_discord_payload(report)
        fields = payload["embeds"][0]["fields"]
        assert len(fields) == 1
        assert ":white_check_mark:" in fields[0]["name"]

    def test_build_discord_payload_no_bug_description(self):
        """Test payload when bug_description is None (no blockquote)."""
        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                    bug_description=None,
                ),
            ],
        )

        payload = notifier._build_discord_payload(report)
        value = payload["embeds"][0]["fields"][0]["value"]
        assert ">" not in value


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

    def test_is_configured_rejects_spoofed_domain(self):
        """Test is_configured rejects URLs with Microsoft domain as substring."""
        notifier = TeamsNotifier(webhook_url="https://evil.com/?redirect=microsoft.com")
        assert notifier.is_configured() is False

    def test_is_configured_rejects_non_microsoft_https(self):
        """Test is_configured rejects non-Microsoft HTTPS URLs."""
        notifier = TeamsNotifier(webhook_url="https://example.com/webhook")
        assert notifier.is_configured() is False

    def test_is_configured_accepts_subdomain(self):
        """Test is_configured accepts valid Microsoft subdomains."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        assert notifier.is_configured() is True

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

    def test_build_teams_payload_structure(self):
        """Test Teams payload uses TextBlocks instead of FactSet."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo/pull/42",
                    pr_title="Fix bug",
                    success=True,
                    bug_description="Off-by-one in loop",
                ),
            ],
            skipped_prs=[
                PRSummary(repo_name="owner/repo2", pr_number=None, pr_url=None,
                          pr_title=None, success=False, error="No fixes"),
            ],
        )

        payload = notifier._build_teams_payload(report)

        assert payload["type"] == "message"
        assert "attachments" in payload
        card = payload["attachments"][0]["content"]
        assert card["type"] == "AdaptiveCard"
        # No FactSet elements
        body_types = [block["type"] for block in card["body"]]
        assert "FactSet" not in body_types
        # All elements are TextBlocks
        assert all(t == "TextBlock" for t in body_types)

    def test_build_teams_payload_bug_description(self):
        """Test Teams payload includes bug description in TextBlock."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo",
                    pr_number=42,
                    pr_url="https://github.com/owner/repo/pull/42",
                    pr_title="Fix bug",
                    success=True,
                    bug_description="Off-by-one in loop",
                ),
            ],
        )

        payload = notifier._build_teams_payload(report)
        card = payload["attachments"][0]["content"]
        # Find the PR TextBlock (after title and summary)
        pr_block = card["body"][2]
        assert "Off-by-one in loop" in pr_block["text"]
        assert "owner/repo" in pr_block["text"]

    def test_build_teams_payload_collapse_skipped(self):
        """Test Teams payload collapses skipped repos."""
        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = _make_report(
            skipped_prs=[
                PRSummary(repo_name=f"owner/repo{i}", pr_number=None, pr_url=None,
                          pr_title=None, success=False, error="No fixes")
                for i in range(5)
            ],
        )

        payload = notifier._build_teams_payload(report)
        card = payload["attachments"][0]["content"]
        # title + summary + 1 collapsed skipped + footer = 4 blocks
        assert len(card["body"]) == 4
        skipped_block = card["body"][2]
        assert "5 repos reviewed with no actionable issues found" in skipped_block["text"]


class TestPlainTextFormat:
    """Tests for plain text report formatting."""

    def _get_notifier(self):
        """Get a concrete notifier for testing format_report."""
        return DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")

    def test_format_report_collapse_skipped(self):
        """Test plain text collapses skipped repos."""
        notifier = self._get_notifier()
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                ),
            ],
            skipped_prs=[
                PRSummary(repo_name=f"owner/skip{i}", pr_number=None, pr_url=None,
                          pr_title=None, success=False, error="No fixes")
                for i in range(3)
            ],
        )

        text = notifier.format_report(report)
        assert "[OK] owner/repo1" in text
        assert "3 repos reviewed with no actionable issues found" in text
        # Individual skipped repos should NOT appear
        assert "owner/skip0" not in text

    def test_format_report_bug_description(self):
        """Test plain text includes bug description."""
        notifier = self._get_notifier()
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                    bug_description="Null check missing",
                ),
            ],
        )

        text = notifier.format_report(report)
        assert "Bug: Null check missing" in text

    def test_format_report_no_skipped(self):
        """Test plain text with no skipped repos omits the skipped line."""
        notifier = self._get_notifier()
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                ),
            ],
        )

        text = notifier.format_report(report)
        assert "no actionable issues" not in text

    def test_format_report_truncates_bug_description(self):
        """Test plain text truncates long bug descriptions."""
        notifier = self._get_notifier()
        long_desc = "B" * 200
        report = _make_report(
            successful_prs=[
                PRSummary(
                    repo_name="owner/repo1",
                    pr_number=1,
                    pr_url="https://github.com/owner/repo1/pull/1",
                    pr_title="Fix A",
                    success=True,
                    bug_description=long_desc,
                ),
            ],
        )

        text = notifier.format_report(report)
        bug_line = [l for l in text.split("\n") if l.strip().startswith("Bug:")][0]
        # "    Bug: " prefix + 120 chars max
        content_after_prefix = bug_line.strip().removeprefix("Bug: ")
        assert len(content_after_prefix) == 120
        assert content_after_prefix.endswith("\u2026")


class TestDiscordRetry:
    """Tests for Discord notification retry."""

    @patch("time.sleep")
    @patch.object(httpx.Client, "post")
    def test_retries_on_http_error(self, mock_post, mock_sleep):
        """Test that send_report retries on HTTP errors."""
        # First two calls raise 500, third succeeds
        error_response = Mock(status_code=500)
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=Mock(), response=error_response
        )
        success_response = Mock(status_code=204)
        success_response.raise_for_status = Mock()
        mock_post.side_effect = [error_response, error_response, success_response]

        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=1,
            prs_created=0,
            prs=[],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 3, 0),
        )

        result = notifier.send_report(report)
        assert result.success is True
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("time.sleep")
    @patch.object(httpx.Client, "post")
    def test_fails_after_max_retries(self, mock_post, mock_sleep):
        """Test that send_report fails after exhausting retries."""
        error_response = Mock(status_code=500)
        error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=Mock(), response=error_response
        )
        mock_post.return_value = error_response

        notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/123/abc")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=1,
            prs_created=0,
            prs=[],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 3, 0),
        )

        result = notifier.send_report(report)
        assert result.success is False
        assert mock_post.call_count == 3


class TestTeamsRetry:
    """Tests for Teams notification retry."""

    @patch("time.sleep")
    @patch.object(httpx.Client, "post")
    def test_retries_on_request_error(self, mock_post, mock_sleep):
        """Test that send_report retries on request errors."""
        mock_post.side_effect = [
            httpx.RequestError("Connection refused", request=Mock()),
            Mock(status_code=200, raise_for_status=Mock()),
        ]

        notifier = TeamsNotifier(webhook_url="https://outlook.office.com/webhook/123")
        report = ReviewReport(
            date=datetime.now(),
            repos_reviewed=1,
            prs_created=0,
            prs=[],
            start_time=datetime(2024, 1, 1, 2, 0),
            end_time=datetime(2024, 1, 1, 3, 0),
        )

        result = notifier.send_report(report)
        assert result.success is True
        assert mock_post.call_count == 2


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
