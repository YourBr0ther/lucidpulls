"""Issue analyzer for prioritizing bugs and enhancements."""

import logging
from dataclasses import dataclass
from typing import Optional

from src.llm.base import BaseLLM
from src.models import GithubIssue

logger = logging.getLogger("lucidpulls.analyzers.issue")


@dataclass
class IssueScore:
    """Scored issue for prioritization."""

    issue: GithubIssue
    score: float
    reason: str


class IssueAnalyzer:
    """Analyzes and prioritizes GitHub issues."""

    def __init__(self, llm: Optional[BaseLLM] = None):
        """Initialize issue analyzer.

        Args:
            llm: Optional LLM for advanced analysis.
        """
        self.llm = llm

    def prioritize(self, issues: list[GithubIssue], limit: int = 5) -> list[GithubIssue]:
        """Prioritize issues for fixing.

        Args:
            issues: List of GithubIssue typed dicts.
            limit: Maximum number of issues to return.

        Returns:
            Sorted list of prioritized issues.
        """
        if not issues:
            return []

        scored = [self._score_issue(issue) for issue in issues]
        scored.sort(key=lambda x: x.score, reverse=True)

        logger.info(f"Prioritized {len(issues)} issues, top {limit}")
        for s in scored[:limit]:
            logger.debug(f"  Issue #{s.issue['number']}: {s.score:.2f} - {s.reason}")

        return [s.issue for s in scored[:limit]]

    def _score_issue(self, issue: GithubIssue) -> IssueScore:
        """Score an issue for priority.

        Args:
            issue: GithubIssue typed dict.

        Returns:
            IssueScore with priority score.
        """
        score = 0.0
        reasons = []

        labels = [l.lower() for l in issue.get("labels", [])]

        # Label-based scoring
        if "bug" in labels:
            score += 3.0
            reasons.append("bug label")
        if "critical" in labels or "urgent" in labels:
            score += 2.0
            reasons.append("critical/urgent")
        if "security" in labels:
            score += 2.5
            reasons.append("security")
        if "enhancement" in labels:
            score += 1.0
            reasons.append("enhancement")
        if "good first issue" in labels:
            score += 0.5
            reasons.append("good first issue")
        if "help wanted" in labels:
            score += 0.5
            reasons.append("help wanted")

        # Content-based scoring
        title = issue.get("title", "").lower()
        body = issue.get("body", "").lower()

        # Keywords that suggest fixable issues
        fixable_keywords = [
            "null pointer", "nullpointerexception", "typeerror",
            "undefined", "none", "attributeerror", "keyerror",
            "off by one", "off-by-one", "index out of",
            "crash", "exception", "error handling",
            "missing check", "validation", "sanitize",
        ]

        for keyword in fixable_keywords:
            if keyword in title or keyword in body:
                score += 0.5
                reasons.append(f"keyword: {keyword}")
                break  # Only count once

        # Penalize vague issues
        if len(body) < 50:
            score -= 0.5
            reasons.append("short description")

        return IssueScore(
            issue=issue,
            score=max(0, score),
            reason=", ".join(reasons) if reasons else "no signals",
        )

    def filter_actionable(self, issues: list[GithubIssue]) -> list[GithubIssue]:
        """Filter issues to only include actionable ones.

        Args:
            issues: List of GithubIssue typed dicts.

        Returns:
            Filtered list of actionable issues.
        """
        actionable = []

        for issue in issues:
            # Skip issues that are likely not actionable
            labels = [l.lower() for l in issue.get("labels", [])]

            # Skip feature requests, questions, etc.
            skip_labels = [
                "question", "discussion", "wontfix", "duplicate",
                "invalid", "blocked", "on hold", "needs info",
            ]
            if any(skip in labels for skip in skip_labels):
                continue

            # Skip if no meaningful description
            body = issue.get("body", "")
            if len(body.strip()) < 20:
                continue

            actionable.append(issue)

        logger.info(f"Filtered to {len(actionable)}/{len(issues)} actionable issues")
        return actionable
