"""Shared data models used across multiple layers."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict


class GithubIssue(TypedDict, total=False):
    """Type definition for GitHub issue dictionaries."""

    number: int
    title: str
    body: str
    labels: list[str]
    url: str
    created_at: str | None


@dataclass
class PRSummary:
    """Summary of a created PR."""

    repo_name: str
    pr_number: int | None
    pr_url: str | None
    pr_title: str | None
    success: bool
    error: str | None = None
    bug_description: str | None = None


@dataclass
class ReviewReport:
    """Complete review report for notifications."""

    date: datetime
    repos_reviewed: int
    prs_created: int
    prs: list[PRSummary]
    start_time: datetime
    end_time: datetime
    llm_tokens_used: int | None = None

    @property
    def duration_str(self) -> str:
        """Get human-readable duration string."""
        delta = self.end_time - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
