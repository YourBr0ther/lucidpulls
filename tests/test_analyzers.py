"""Tests for code and issue analyzers."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.analyzers.base import (
    AnalysisResult,
    FixSuggestion,
    SCORE_ENTRY_POINT,
    SCORE_IMPORTANT_NAME,
    SCORE_SOURCE_DIR,
    SCORE_LOW_PRIORITY_DIR,
    SCORE_TEST_FILE,
    SCORE_INIT_FILE,
)
from src.analyzers.code_analyzer import CodeAnalyzer, LLMFixResponse
from src.analyzers.issue_analyzer import IssueAnalyzer


class TestFixSuggestion:
    """Tests for FixSuggestion."""

    def test_is_high_confidence(self):
        """Test high confidence detection."""
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )
        assert fix.is_high_confidence is True

    def test_is_not_high_confidence(self):
        """Test non-high confidence."""
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="medium",
        )
        assert fix.is_high_confidence is False


class TestAnalysisResult:
    """Tests for AnalysisResult."""

    def test_success_without_error(self):
        """Test success property without error."""
        result = AnalysisResult(repo_name="test", found_fix=False)
        assert result.success is True

    def test_success_with_error(self):
        """Test success property with error."""
        result = AnalysisResult(repo_name="test", found_fix=False, error="Error")
        assert result.success is False


class TestCodeAnalyzer:
    """Tests for CodeAnalyzer."""

    def test_init(self):
        """Test initialization."""
        mock_llm = Mock()
        analyzer = CodeAnalyzer(mock_llm)
        assert analyzer.llm is mock_llm

    def test_extract_json_from_code_fence(self):
        """Test JSON extraction from code fence."""
        analyzer = CodeAnalyzer(Mock())
        text = """Here's my analysis:
```json
{"found_bug": true, "file_path": "test.py"}
```
"""
        result = analyzer._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data["found_bug"] is True

    def test_extract_json_raw(self):
        """Test JSON extraction from raw text."""
        analyzer = CodeAnalyzer(Mock())
        text = 'Some text {"found_bug": false} more text'
        result = analyzer._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data["found_bug"] is False

    def test_extract_json_no_json(self):
        """Test JSON extraction with no JSON."""
        analyzer = CodeAnalyzer(Mock())
        result = analyzer._extract_json("No JSON here")
        assert result is None

    def test_fix_json_newlines_double_backslash(self):
        """Test _fix_json_newlines handles double-backslash before quote correctly."""
        analyzer = CodeAnalyzer(Mock())
        # The \\\" sequence is an escaped backslash followed by an unescaped quote.
        # The method must NOT treat the quote as escaped.
        text = '{"path": "C:\\\\Users\\\\test", "key": "value"}'
        result = analyzer._fix_json_newlines(text)
        # Should be unchanged - no bare newlines to fix
        assert result == text
        # Verify it's still valid JSON
        data = json.loads(result)
        assert data["key"] == "value"

    def test_fix_json_newlines_bare_newline(self):
        """Test _fix_json_newlines escapes bare newlines inside strings."""
        analyzer = CodeAnalyzer(Mock())
        text = '{"desc": "line1\nline2"}'
        result = analyzer._fix_json_newlines(text)
        assert result == '{"desc": "line1\\nline2"}'

    def test_parse_llm_response_valid(self):
        """Test parsing valid LLM response."""
        analyzer = CodeAnalyzer(Mock())
        response = json.dumps({
            "found_bug": True,
            "file_path": "src/main.py",
            "bug_description": "Null check missing",
            "fix_description": "Added null check",
            "original_code": "x.value",
            "fixed_code": "x.value if x else None",
            "pr_title": "Fix null pointer",
            "pr_body": "Added null check",
            "confidence": "high",
        })

        fix = analyzer._parse_llm_response(response)
        assert fix is not None
        assert fix.file_path == "src/main.py"
        assert fix.confidence == "high"

    def test_parse_llm_response_no_bug(self):
        """Test parsing response with no bug found."""
        analyzer = CodeAnalyzer(Mock())
        response = json.dumps({"found_bug": False})

        fix = analyzer._parse_llm_response(response)
        assert fix is None

    def test_parse_llm_response_low_confidence(self):
        """Test parsing low confidence response."""
        analyzer = CodeAnalyzer(Mock())
        response = json.dumps({
            "found_bug": True,
            "file_path": "src/main.py",
            "bug_description": "Bug",
            "fix_description": "Fix",
            "original_code": "old",
            "fixed_code": "new",
            "pr_title": "Title",
            "pr_body": "Body",
            "confidence": "low",
        })

        fix = analyzer._parse_llm_response(response)
        assert fix is None  # Low confidence is rejected

    def test_format_issues(self):
        """Test issue formatting."""
        analyzer = CodeAnalyzer(Mock())
        issues = [
            {
                "number": 1,
                "title": "Bug 1",
                "body": "Description",
                "labels": ["bug"],
            }
        ]

        result = analyzer._format_issues(issues)
        assert "Issue #1" in result
        assert "Bug 1" in result
        assert "bug" in result

    def test_format_issues_empty(self):
        """Test issue formatting with empty list."""
        analyzer = CodeAnalyzer(Mock())
        result = analyzer._format_issues([])
        assert result == "No open issues."

    def test_apply_fix_file_not_found(self):
        """Test applying fix to non-existent file."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="nonexistent.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False

    def test_apply_fix_success(self):
        """Test successfully applying fix."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="value = x.data",
            fixed_code="value = x.data if x else None",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("def func():\n    value = x.data\n    return value\n")

            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is True

            new_content = test_file.read_text()
            assert "value = x.data if x else None" in new_content


class TestCodeAnalyzerApplyFixSecurity:
    """Security tests for CodeAnalyzer.apply_fix."""

    def test_apply_fix_prevents_absolute_path_traversal(self):
        """Test that absolute path traversal is blocked."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="/etc/passwd",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False

    def test_apply_fix_prevents_relative_path_traversal(self):
        """Test that relative path traversal via .. is blocked."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="../../etc/passwd",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False

    def test_apply_fix_prevents_mixed_separator_traversal(self):
        """Test that mixed separator path traversal (e.g. src/..\\etc) is blocked."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="src/..\\etc/passwd",
            bug_description="Bug",
            fix_description="Fix",
            original_code="old",
            fixed_code="new",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False

    def test_llm_response_validator_rejects_mixed_separator_traversal(self):
        """Test that the Pydantic validator rejects mixed-separator traversal."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Suspicious file_path"):
            LLMFixResponse(
                found_bug=True,
                file_path="src/..\\etc/passwd",
                bug_description="Bug",
                fix_description="Fix",
                original_code="old",
                fixed_code="new",
                pr_title="Title",
                pr_body="Body",
                confidence="high",
            )

    def test_apply_fix_rejects_multiple_matches(self):
        """Test that ambiguous fixes with multiple matches are rejected."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="x = 1",
            fixed_code="x = 2",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("x = 1\ny = 2\nx = 1\n")

            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False
            # Verify file was NOT modified
            assert test_file.read_text() == "x = 1\ny = 2\nx = 1\n"

    def test_apply_fix_reverts_on_invalid_python_syntax(self):
        """Test that fix is reverted when it produces invalid Python."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="x = 1",
            fixed_code="x = (",  # Invalid syntax
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            original = "x = 1\n"
            test_file.write_text(original)

            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False
            # Verify file was reverted
            assert test_file.read_text() == original

    def test_apply_fix_original_code_not_found(self):
        """Test behavior when original code doesn't match."""
        analyzer = CodeAnalyzer(Mock())
        fix = FixSuggestion(
            file_path="test.py",
            bug_description="Bug",
            fix_description="Fix",
            original_code="not_in_file",
            fixed_code="new_code",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("some other content\n")

            result = analyzer.apply_fix(Path(tmpdir), fix)
            assert result is False


class TestIssueAnalyzer:
    """Tests for IssueAnalyzer."""

    def test_prioritize_empty(self):
        """Test prioritizing empty list."""
        analyzer = IssueAnalyzer()
        result = analyzer.prioritize([])
        assert result == []

    def test_prioritize_bug_first(self):
        """Test bugs are prioritized."""
        analyzer = IssueAnalyzer()
        issues = [
            {"number": 1, "title": "Enhancement", "body": "Add feature", "labels": ["enhancement"]},
            {"number": 2, "title": "Bug", "body": "Something is broken", "labels": ["bug"]},
        ]

        result = analyzer.prioritize(issues)
        assert result[0]["number"] == 2  # Bug should be first

    def test_prioritize_critical_highest(self):
        """Test critical issues are highest priority."""
        analyzer = IssueAnalyzer()
        issues = [
            {"number": 1, "title": "Regular bug", "body": "Bug", "labels": ["bug"]},
            {"number": 2, "title": "Critical bug", "body": "Bug", "labels": ["bug", "critical"]},
        ]

        result = analyzer.prioritize(issues)
        assert result[0]["number"] == 2  # Critical should be first

    def test_prioritize_security_high(self):
        """Test security issues are high priority."""
        analyzer = IssueAnalyzer()
        issues = [
            {"number": 1, "title": "Enhancement", "body": "Feature", "labels": ["enhancement"]},
            {"number": 2, "title": "Security issue", "body": "Vulnerability", "labels": ["security"]},
        ]

        result = analyzer.prioritize(issues)
        assert result[0]["number"] == 2

    def test_filter_actionable(self):
        """Test filtering actionable issues."""
        analyzer = IssueAnalyzer()
        issues = [
            {"number": 1, "title": "Bug", "body": "A real bug with details", "labels": ["bug"]},
            {"number": 2, "title": "Question", "body": "How do I?", "labels": ["question"]},
            {"number": 3, "title": "Duplicate", "body": "Same as #1", "labels": ["duplicate"]},
            {"number": 4, "title": "No body", "body": "", "labels": ["bug"]},
        ]

        result = analyzer.filter_actionable(issues)
        assert len(result) == 1
        assert result[0]["number"] == 1

    def test_score_fixable_keywords(self):
        """Test scoring based on fixable keywords."""
        analyzer = IssueAnalyzer()

        issue1 = {"number": 1, "title": "Random issue", "body": "Something that is unrelated to any known bug pattern in the codebase", "labels": []}
        issue2 = {"number": 2, "title": "Null pointer", "body": "NPE crash when accessing the user object after logout from the system", "labels": []}

        score1 = analyzer._score_issue(issue1)
        score2 = analyzer._score_issue(issue2)

        assert score2.score > score1.score


class TestFileScoring:
    """Tests for BaseAnalyzer._score_file priority heuristics."""

    def _score(self, rel_path_str: str, file_size: int = 5000) -> int:
        """Helper to score a file path."""
        analyzer = CodeAnalyzer(Mock())
        return analyzer._score_file(Path(rel_path_str), file_size)

    def test_entry_points_score_higher_than_regular(self):
        """Entry point files (main, app, index) should score higher."""
        entry_score = self._score("main.py")
        regular_score = self._score("foo.py")
        assert entry_score > regular_score

    def test_source_dirs_score_higher_than_root(self):
        """Files in src/ should score higher than files at root."""
        src_score = self._score("src/foo.py")
        root_score = self._score("foo.py")
        assert src_score > root_score

    def test_test_files_score_lower_than_production(self):
        """Test files should score lower than production files."""
        prod_score = self._score("src/handler.py")
        test_score = self._score("tests/test_handler.py")
        assert prod_score > test_score

    def test_spec_files_detected_as_tests(self):
        """Files with .spec. in name should be penalized as tests."""
        regular_score = self._score("src/handler.ts")
        spec_score = self._score("src/handler.spec.ts")
        assert regular_score > spec_score

    def test_dot_test_files_detected_as_tests(self):
        """Files with .test. in name should be penalized as tests."""
        regular_score = self._score("src/handler.js")
        test_score = self._score("src/handler.test.js")
        assert regular_score > test_score

    def test_deeper_paths_score_lower(self):
        """Files deeper in the tree should score lower than shallow ones."""
        shallow = self._score("src/main.py")
        deep = self._score("src/a/b/c/main.py")
        assert shallow > deep

    def test_sweet_spot_size_beats_tiny(self):
        """Files in the sweet-spot size range should outscore tiny files."""
        sweet = self._score("foo.py", file_size=5000)
        tiny = self._score("foo.py", file_size=50)
        assert sweet > tiny

    def test_init_files_penalized(self):
        """__init__.py files should be penalized."""
        init_score = self._score("src/__init__.py")
        regular_score = self._score("src/models.py")
        assert regular_score > init_score

    def test_examples_dir_penalized(self):
        """Files in examples/ should score lower."""
        src_score = self._score("src/handler.py")
        examples_score = self._score("examples/handler.py")
        assert src_score > examples_score

    def test_conftest_detected_as_test(self):
        """conftest.py should be penalized as a test file."""
        regular_score = self._score("src/config.py")
        conftest_score = self._score("tests/conftest.py")
        assert regular_score > conftest_score

    def test_important_names_score_higher(self):
        """Architecturally important filenames should score higher."""
        important_score = self._score("models.py")
        regular_score = self._score("foo.py")
        assert important_score > regular_score
