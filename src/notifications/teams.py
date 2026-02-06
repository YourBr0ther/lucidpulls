"""Microsoft Teams webhook notification client."""

import logging

import httpx

from src.models import ReviewReport
from src.notifications.base import BaseNotifier, NotificationResult
from src.utils import retry

logger = logging.getLogger("lucidpulls.notifications.teams")


class TeamsNotifier(BaseNotifier):
    """Microsoft Teams webhook notification sender."""

    def __init__(self, webhook_url: str):
        """Initialize Teams notifier.

        Args:
            webhook_url: Teams webhook URL.
        """
        self.webhook_url = webhook_url
        self._client = httpx.Client(timeout=30.0)

    def send_report(self, report: ReviewReport) -> NotificationResult:
        """Send a review report to Teams.

        Args:
            report: Review report to send.

        Returns:
            NotificationResult with success status.
        """
        if not self.is_configured():
            return NotificationResult(success=False, error="Teams webhook not configured")

        try:
            payload = self._build_teams_payload(report)
            self._send_with_retry(payload)

            logger.info("Successfully sent report to Teams")
            return NotificationResult(success=True)

        except httpx.HTTPStatusError as e:
            error = f"Teams HTTP error: {e.response.status_code}"
            logger.error(error)
            return NotificationResult(success=False, error=error)
        except httpx.RequestError as e:
            error = f"Teams request error: {e}"
            logger.error(error)
            return NotificationResult(success=False, error=error)

    @retry(max_attempts=3, delay=2.0, backoff=2.0,
           exceptions=(httpx.HTTPStatusError, httpx.RequestError))
    def _send_with_retry(self, payload: dict) -> None:
        response = self._client.post(self.webhook_url, json=payload)
        response.raise_for_status()

    @staticmethod
    def _truncate(text: str, max_len: int = 120) -> str:
        """Truncate text to max_len, adding ellipsis if needed."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "\u2026"

    def _build_teams_payload(self, report: ReviewReport) -> dict:
        """Build Teams Adaptive Card payload.

        Args:
            report: Review report.

        Returns:
            Teams webhook payload.
        """
        date_str = report.date.strftime("%Y-%m-%d")
        start_str = report.start_time.strftime("%H:%M")
        end_str = report.end_time.strftime("%H:%M")

        # Separate successful PRs from skipped repos
        successful = [pr for pr in report.prs if pr.success]
        skipped = [pr for pr in report.prs if not pr.success]

        # Build TextBlock elements for successful PRs
        pr_blocks = []
        for pr in successful:
            text = f"✅ **{pr.repo_name}** — [PR #{pr.pr_number}]({pr.pr_url}): {pr.pr_title}"
            if pr.bug_description:
                text += f"\n\n> {self._truncate(pr.bug_description)}"
            pr_blocks.append({
                "type": "TextBlock",
                "text": text,
                "wrap": True,
            })

        # Collapse skipped repos into a single TextBlock
        if skipped:
            count = len(skipped)
            noun = "repo" if count == 1 else "repos"
            pr_blocks.append({
                "type": "TextBlock",
                "text": f"⏭️ {count} {noun} reviewed with no actionable issues found",
                "wrap": True,
                "isSubtle": True,
            })

        # Build Adaptive Card
        body = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"\U0001f305 LucidPulls Morning Report - {date_str}",
            },
            {
                "type": "TextBlock",
                "text": (
                    f"\U0001f4ca **Summary:** {report.repos_reviewed} repositories "
                    f"reviewed, {report.prs_created} PRs created"
                ),
                "wrap": True,
            },
            *pr_blocks,
            {
                "type": "TextBlock",
                "text": (
                    f"Review window: {start_str} - {end_str} ({report.duration_str})"
                    + (f" | Tokens: {report.llm_tokens_used:,}" if report.llm_tokens_used else "")
                ),
                "size": "Small",
                "isSubtle": True,
            },
        ]

        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": body,
                    },
                }
            ],
        }

        return card

    def is_configured(self) -> bool:
        """Check if Teams webhook is configured.

        Returns:
            True if webhook URL is set and points to a valid Microsoft domain.
        """
        if not self.webhook_url or not self.webhook_url.startswith("https://"):
            return False

        try:
            from urllib.parse import urlparse
            hostname = urlparse(self.webhook_url).hostname or ""
            valid_domains = (".office.com", ".office365.com", ".microsoft.com")
            return any(hostname == d.lstrip(".") or hostname.endswith(d) for d in valid_domains)
        except Exception:
            return False

    @property
    def channel_name(self) -> str:
        """Get channel name."""
        return "Microsoft Teams"

    def close(self) -> None:
        """Close the HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
