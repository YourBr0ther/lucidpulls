"""Microsoft Teams webhook notification client."""

import logging

import httpx

from src.notifications.base import BaseNotifier, NotificationResult, ReviewReport

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
            response = self._client.post(self.webhook_url, json=payload)
            response.raise_for_status()

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

        # Build facts for each PR
        facts = []
        for pr in report.prs:
            if pr.success:
                facts.append({
                    "title": f"✅ {pr.repo_name}",
                    "value": f"[PR #{pr.pr_number}]({pr.pr_url}): {pr.pr_title}",
                })
            else:
                value = pr.error if pr.error else "No actionable fixes identified"
                facts.append({
                    "title": f"⏭️ {pr.repo_name}",
                    "value": value,
                })

        # Build Adaptive Card
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
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Large",
                                "weight": "Bolder",
                                "text": f"🌅 LucidPulls Morning Report - {date_str}",
                            },
                            {
                                "type": "TextBlock",
                                "text": (
                                    f"📊 **Summary:** {report.repos_reviewed} repositories "
                                    f"reviewed, {report.prs_created} PRs created"
                                ),
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": facts,
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Review window: {start_str} - {end_str} ({report.duration_str})",
                                "size": "Small",
                                "isSubtle": True,
                            },
                        ],
                    },
                }
            ],
        }

        return card

    def is_configured(self) -> bool:
        """Check if Teams webhook is configured.

        Returns:
            True if webhook URL is set.
        """
        return bool(self.webhook_url and "webhook" in self.webhook_url.lower())

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

    def __del__(self):
        """Clean up HTTP client."""
        self.close()
