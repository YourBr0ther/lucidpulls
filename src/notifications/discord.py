"""Discord webhook notification client."""

import logging

import httpx

from src.models import ReviewReport
from src.notifications.base import BaseNotifier, NotificationResult
from src.utils import retry

logger = logging.getLogger("lucidpulls.notifications.discord")


class DiscordNotifier(BaseNotifier):
    """Discord webhook notification sender."""

    def __init__(self, webhook_url: str):
        """Initialize Discord notifier.

        Args:
            webhook_url: Discord webhook URL.
        """
        self.webhook_url = webhook_url
        self._client = httpx.Client(timeout=30.0)

    def send_report(self, report: ReviewReport) -> NotificationResult:
        """Send a review report to Discord.

        Args:
            report: Review report to send.

        Returns:
            NotificationResult with success status.
        """
        if not self.is_configured():
            return NotificationResult(success=False, error="Discord webhook not configured")

        try:
            payload = self._build_discord_payload(report)
            self._send_with_retry(payload)

            logger.info("Successfully sent report to Discord")
            return NotificationResult(success=True)

        except httpx.HTTPStatusError as e:
            error = f"Discord HTTP error: {e.response.status_code}"
            logger.error(error)
            return NotificationResult(success=False, error=error)
        except httpx.RequestError as e:
            error = f"Discord request error: {e}"
            logger.error(error)
            return NotificationResult(success=False, error=error)

    @retry(max_attempts=3, delay=2.0, backoff=2.0,
           exceptions=(httpx.HTTPStatusError, httpx.RequestError))
    def _send_with_retry(self, payload: dict) -> None:
        response = self._client.post(self.webhook_url, json=payload)
        # Fail fast on non-retryable 4xx (bad webhook URL, malformed payload, etc.)
        if 400 <= response.status_code < 500 and response.status_code != 429:
            raise ValueError(f"Discord HTTP {response.status_code}: {response.text[:200]}")
        response.raise_for_status()  # 429/5xx — retried by decorator

    def _build_discord_payload(self, report: ReviewReport) -> dict:
        """Build Discord webhook payload with embeds.

        Args:
            report: Review report.

        Returns:
            Discord webhook payload.
        """
        date_str = report.date.strftime("%Y-%m-%d")

        # Separate successful PRs from skipped repos
        successful = [pr for pr in report.prs if pr.success]
        skipped = [pr for pr in report.prs if not pr.success]

        # Build embed fields for successful PRs only
        fields = []
        for pr in successful:
            value = f"[PR #{pr.pr_number}]({pr.pr_url}): {pr.pr_title}"
            if pr.bug_description:
                value += f"\n> {self._truncate(pr.bug_description)}"
            fields.append({
                "name": f":white_check_mark: {pr.repo_name}",
                "value": value,
                "inline": False,
            })

        # Collapse skipped repos into a single line
        if skipped:
            count = len(skipped)
            noun = "repo" if count == 1 else "repos"
            fields.append({
                "name": ":fast_forward: Skipped",
                "value": f"{count} {noun} reviewed with no actionable issues found",
                "inline": False,
            })

        # Build footer
        start_str = report.start_time.strftime("%H:%M")
        end_str = report.end_time.strftime("%H:%M")
        footer = f"Review window: {start_str} - {end_str} ({report.duration_str})"
        if report.llm_tokens_used:
            footer += f" | Tokens: {report.llm_tokens_used:,}"

        embed = {
            "title": f":sunrise: LucidPulls Morning Report - {date_str}",
            "description": (
                f":bar_chart: **Summary:** {report.repos_reviewed} repositories reviewed, "
                f"{report.prs_created} PRs created"
            ),
            "color": 0x00D26A if report.prs_created > 0 else 0x808080,  # Green or gray
            "fields": fields,
            "footer": {"text": footer},
        }

        return {
            "embeds": [embed],
        }

    def is_configured(self) -> bool:
        """Check if Discord webhook is configured.

        Returns:
            True if webhook URL is set.
        """
        return bool(self.webhook_url and self.webhook_url.startswith("https://discord.com/"))

    @property
    def channel_name(self) -> str:
        """Get channel name."""
        return "Discord"

    def close(self) -> None:
        """Close the HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
