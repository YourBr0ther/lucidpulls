"""Shared data models used across multiple layers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict


class GithubIssue(TypedDict, total=False):
    """Type definition for GitHub issue dictionaries."""

    number: int
    title: str
    body: str
    labels: list[str]
    url: str
    created_at: Optional[str]


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
