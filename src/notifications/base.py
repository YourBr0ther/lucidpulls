"""Base notification interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.models import PRSummary, ReviewReport


@dataclass
class NotificationResult:
    """Result of sending a notification."""

    success: bool
    error: str | None = None


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

    @staticmethod
    def _truncate(text: str, max_len: int = 120) -> str:
        """Truncate text to max_len, adding ellipsis if needed."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "\u2026"

    def format_report(self, report: ReviewReport) -> str:
        """Format a report as plain text.

        Args:
            report: Review report.

        Returns:
            Formatted report string.
        """
        date_str = report.date.strftime("%Y-%m-%d")

        successful = [pr for pr in report.prs if pr.success]
        skipped = [pr for pr in report.prs if not pr.success]

        lines = [
            f"LucidPulls Morning Report - {date_str}",
            "",
            f"Summary: {report.repos_reviewed} repositories reviewed, "
            f"{report.prs_created} PRs created",
            "",
        ]

        for pr in successful:
            lines.append(f"[OK] {pr.repo_name}")
            lines.append(f"    PR #{pr.pr_number}: {pr.pr_title}")
            lines.append(f"    {pr.pr_url}")
            if pr.bug_description:
                lines.append(f"    Bug: {self._truncate(pr.bug_description)}")
            lines.append("")

        if skipped:
            count = len(skipped)
            noun = "repo" if count == 1 else "repos"
            lines.append(f"[--] {count} {noun} reviewed with no actionable issues found")
            lines.append("")

        lines.append("---")
        start_str = report.start_time.strftime("%H:%M")
        end_str = report.end_time.strftime("%H:%M")
        lines.append(f"Review window: {start_str} - {end_str} ({report.duration_str})")

        return "\n".join(lines)
