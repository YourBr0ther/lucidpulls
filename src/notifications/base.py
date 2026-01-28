"""Base notification interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class NotificationResult:
    """Result of sending a notification."""

    success: bool
    error: Optional[str] = None


@dataclass
class PRSummary:
    """Summary of a created PR."""

    repo_name: str
    pr_number: Optional[int]
    pr_url: Optional[str]
    pr_title: Optional[str]
    success: bool
    error: Optional[str] = None


@dataclass
class ReviewReport:
    """Complete review report for notifications."""

    date: datetime
    repos_reviewed: int
    prs_created: int
    prs: list[PRSummary]
    start_time: datetime
    end_time: datetime

    @property
    def duration_str(self) -> str:
        """Get human-readable duration string."""
        delta = self.end_time - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


class BaseNotifier(ABC):
    """Abstract base class for notification channels."""

    @abstractmethod
    def send_report(self, report: ReviewReport) -> NotificationResult:
        """Send a review report notification.

        Args:
            report: Review report to send.

        Returns:
            NotificationResult with success status.
        """
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the notifier is properly configured.

        Returns:
            True if configured and ready to send.
        """
        pass

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Get the name of this notification channel.

        Returns:
            Human-readable channel name.
        """
        pass

    def format_report(self, report: ReviewReport) -> str:
        """Format a report as plain text.

        Args:
            report: Review report.

        Returns:
            Formatted report string.
        """
        date_str = report.date.strftime("%Y-%m-%d")

        lines = [
            f"LucidPulls Morning Report - {date_str}",
            "",
            f"Summary: {report.repos_reviewed} repositories reviewed, "
            f"{report.prs_created} PRs created",
            "",
        ]

        for pr in report.prs:
            if pr.success:
                lines.append(f"[OK] {pr.repo_name}")
                lines.append(f"    PR #{pr.pr_number}: {pr.pr_title}")
                lines.append(f"    {pr.pr_url}")
            else:
                lines.append(f"[--] {pr.repo_name}")
                if pr.error:
                    lines.append(f"    {pr.error}")
                else:
                    lines.append("    No actionable fixes identified")
            lines.append("")

        lines.append("---")
        start_str = report.start_time.strftime("%H:%M")
        end_str = report.end_time.strftime("%H:%M")
        lines.append(f"Review window: {start_str} - {end_str} ({report.duration_str})")

        return "\n".join(lines)
