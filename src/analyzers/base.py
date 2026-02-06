"""Base analyzer interface and data models."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.models import GithubIssue

logger = logging.getLogger("lucidpulls.analyzers.base")

# Constants for file discovery
MAX_FILES = 50  # Maximum number of files to analyze
MAX_FILE_SIZE = 100_000  # Maximum file size in bytes (100KB)
MAX_CHARS_FOR_LLM = 50_000  # Maximum characters to send to LLM

# File priority scoring constants
ENTRY_POINT_NAMES = frozenset({
    "main", "app", "index", "server", "cli", "run", "manage", "wsgi", "asgi",
})
IMPORTANT_NAMES = frozenset({
    "models", "routes", "views", "config", "settings", "urls", "schema",
    "database", "db", "api", "auth", "middleware", "handlers", "services",
    "utils", "helpers", "core", "base",
})
SOURCE_DIRS = frozenset({
    "src", "lib", "app", "pkg", "internal", "cmd",
})
LOW_PRIORITY_DIRS = frozenset({
    "tests", "test", "testing", "spec", "specs",
    "examples", "example", "samples", "sample",
    "docs", "doc", "documentation",
    "migrations", "migrate", "alembic",
    "fixtures", "testdata", "test_data",
    "benchmarks", "bench",
    "scripts", "tools",
    "vendor", "third_party",
})

# Score weights
SCORE_ENTRY_POINT = 20
SCORE_IMPORTANT_NAME = 10
SCORE_SOURCE_DIR = 5
SCORE_LOW_PRIORITY_DIR = -10
SCORE_TEST_FILE = -15
SCORE_DEPTH_PENALTY = -1  # per directory level
SCORE_SWEET_SPOT_SIZE = 3  # 500B-20KB
SCORE_TINY_FILE = -3  # < 100B
SCORE_LARGE_FILE = -2  # > 50KB
SCORE_INIT_FILE = -5


@dataclass
class FixSuggestion:
    """A suggested fix for a bug."""

    file_path: str
    bug_description: str
    fix_description: str
    original_code: str
    fixed_code: str
    pr_title: str
    pr_body: str
    confidence: str  # high, medium, low
    related_issue: Optional[int] = None

    @property
    def is_high_confidence(self) -> bool:
        """Check if this is a high-confidence fix."""
        return self.confidence.lower() == "high"


@dataclass
class AnalysisResult:
    """Result of repository analysis."""

    repo_name: str
    found_fix: bool
    fix: Optional[FixSuggestion] = None
    error: Optional[str] = None
    files_analyzed: int = 0
    issues_reviewed: int = 0
    analysis_time_seconds: float = 0.0
    llm_tokens_used: Optional[int] = None

    @property
    def success(self) -> bool:
        """Check if analysis completed successfully (even if no fix found)."""
        return self.error is None


class BaseAnalyzer(ABC):
    """Abstract base class for code analyzers."""

    @abstractmethod
    def analyze(
        self,
        repo_path: Path,
        repo_name: str,
        issues: Optional[list[GithubIssue]] = None,
    ) -> AnalysisResult:
        """Analyze a repository for potential fixes.

        Args:
            repo_path: Local path to the cloned repository.
            repo_name: Full repository name (owner/repo).
            issues: Optional list of open issues to consider.

        Returns:
            AnalysisResult with findings.
        """
        pass

    def _score_file(self, rel_path: Path, file_size: int) -> int:
        """Score a file by importance heuristics.

        Higher scores indicate more important files that should be
        analyzed first.

        Args:
            rel_path: Path relative to repository root.
            file_size: File size in bytes.

        Returns:
            Integer score.
        """
        score = 0
        stem = rel_path.stem.lower()
        parts = [p.lower() for p in rel_path.parts]
        dirs = parts[:-1]  # directory components only

        # Entry point bonus
        if stem in ENTRY_POINT_NAMES:
            score += SCORE_ENTRY_POINT

        # Important architectural file bonus
        if stem in IMPORTANT_NAMES:
            score += SCORE_IMPORTANT_NAME

        # __init__ files are usually boilerplate
        if stem == "__init__":
            score += SCORE_INIT_FILE

        # Source directory bonus
        if any(d in SOURCE_DIRS for d in dirs):
            score += SCORE_SOURCE_DIR

        # Low-priority directory penalty
        if any(d in LOW_PRIORITY_DIRS for d in dirs):
            score += SCORE_LOW_PRIORITY_DIR

        # Test file penalty (check filename patterns)
        filename = rel_path.name.lower()
        if (
            filename.startswith("test_")
            or filename.endswith("_test.py")
            or filename.endswith("_test.js")
            or filename.endswith("_test.ts")
            or filename.endswith("_test.go")
            or ".spec." in filename
            or ".test." in filename
            or filename == "conftest.py"
        ):
            score += SCORE_TEST_FILE

        # Depth penalty: deeper files are less likely to be core
        depth = len(dirs)
        score += depth * SCORE_DEPTH_PENALTY

        # Size heuristics
        if 500 <= file_size <= 20_000:
            score += SCORE_SWEET_SPOT_SIZE
        elif file_size < 100:
            score += SCORE_TINY_FILE
        elif file_size > 50_000:
            score += SCORE_LARGE_FILE

        return score

    def _get_code_files(
        self,
        repo_path: Path,
        extensions: Optional[list[str]] = None,
        max_files: int = MAX_FILES,
        max_file_size: int = MAX_FILE_SIZE,
    ) -> list[tuple[Path, str]]:
        """Get code files from a repository, prioritized by importance.

        Uses a collect-score-sort-read approach:
        1. Walk all eligible files collecting only metadata (cheap stat)
        2. Score and sort by importance heuristics
        3. Read content only for the top max_files

        Args:
            repo_path: Path to repository.
            extensions: File extensions to include (default: common code files).
            max_files: Maximum number of files to return.
            max_file_size: Maximum file size in bytes.

        Returns:
            List of (path, content) tuples, ordered by priority.
        """
        if extensions is None:
            extensions = [
                ".py", ".js", ".ts", ".jsx", ".tsx",
                ".java", ".go", ".rs", ".rb", ".php",
                ".c", ".cpp", ".h", ".hpp", ".cs",
            ]

        # Convert to set for O(1) lookup
        extension_set = set(extensions)

        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "target", "vendor",
        }

        # Phase 1: Collect all eligible files with metadata only
        candidates: list[tuple[Path, int, int]] = []  # (rel_path, size, score)

        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in extension_set:
                continue
            if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                continue

            try:
                file_size = file_path.stat().st_size
                if file_size > max_file_size:
                    continue

                rel_path = file_path.relative_to(repo_path)
                score = self._score_file(rel_path, file_size)
                candidates.append((rel_path, file_size, score))
            except OSError:
                continue

        # Phase 2: Sort by score descending, take top max_files
        candidates.sort(key=lambda c: c[2], reverse=True)
        selected = candidates[:max_files]

        # Phase 3: Read content only for selected files
        files = []
        for rel_path, _size, _score in selected:
            try:
                full_path = repo_path / rel_path
                content = full_path.read_text(encoding="utf-8", errors="ignore")
                files.append((rel_path, content))
            except (OSError, UnicodeDecodeError):
                continue

        return files

    def _format_code_for_llm(
        self, files: list[tuple[Path, str]], max_chars: int = MAX_CHARS_FOR_LLM
    ) -> str:
        """Format code files for LLM consumption.

        Args:
            files: List of (path, content) tuples.
            max_chars: Maximum total characters.

        Returns:
            Formatted string with file contents.
        """
        result = []
        total_chars = 0

        for idx, (path, content) in enumerate(files):
            header = f"\n--- {path} ---\n"
            if total_chars + len(header) + len(content) > max_chars:
                # Truncate if needed
                remaining = max_chars - total_chars - len(header) - 100
                if remaining > 500:
                    result.append(header)
                    result.append(content[:remaining])
                    result.append("\n... [truncated]")
                skipped = len(files) - idx - (1 if remaining > 500 else 0)
                if skipped > 0:
                    logger.warning(
                        f"Skipped {skipped}/{len(files)} files due to size limit "
                        f"({max_chars} chars)"
                    )
                break

            result.append(header)
            result.append(content)
            total_chars += len(header) + len(content)

        return "".join(result)
