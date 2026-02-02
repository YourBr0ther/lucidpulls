"""Base analyzer interface and data models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.models import GithubIssue

# Constants for file discovery
MAX_FILES = 50  # Maximum number of files to analyze
MAX_FILE_SIZE = 100_000  # Maximum file size in bytes (100KB)
MAX_CHARS_FOR_LLM = 50_000  # Maximum characters to send to LLM


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

    def _get_code_files(
        self,
        repo_path: Path,
        extensions: Optional[list[str]] = None,
        max_files: int = MAX_FILES,
        max_file_size: int = MAX_FILE_SIZE,
    ) -> list[tuple[Path, str]]:
        """Get code files from a repository.

        Args:
            repo_path: Path to repository.
            extensions: File extensions to include (default: common code files).
            max_files: Maximum number of files to return.
            max_file_size: Maximum file size in bytes.

        Returns:
            List of (path, content) tuples.
        """
        if extensions is None:
            extensions = [
                ".py", ".js", ".ts", ".jsx", ".tsx",
                ".java", ".go", ".rs", ".rb", ".php",
                ".c", ".cpp", ".h", ".hpp", ".cs",
            ]

        # Convert to set for O(1) lookup
        extension_set = set(extensions)

        files = []
        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "target", "vendor",
        }

        # Walk the tree once and filter by extension in memory
        for file_path in repo_path.rglob("*"):
            # Skip if not a file or wrong extension
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in extension_set:
                continue

            # Skip ignored directories
            if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                continue

            # Skip large files
            try:
                if file_path.stat().st_size > max_file_size:
                    continue

                content = file_path.read_text(encoding="utf-8", errors="ignore")
                rel_path = file_path.relative_to(repo_path)
                files.append((rel_path, content))

                if len(files) >= max_files:
                    return files
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

        for path, content in files:
            header = f"\n--- {path} ---\n"
            if total_chars + len(header) + len(content) > max_chars:
                # Truncate if needed
                remaining = max_chars - total_chars - len(header) - 100
                if remaining > 500:
                    result.append(header)
                    result.append(content[:remaining])
                    result.append("\n... [truncated]")
                break

            result.append(header)
            result.append(content)
            total_chars += len(header) + len(content)

        return "".join(result)
