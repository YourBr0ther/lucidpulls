"""Tests for code and issue analyzers."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.analyzers.base import AnalysisResult, FixSuggestion
from src.analyzers.code_analyzer import CodeAnalyzer
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

        issue1 = {"number": 1, "title": "Random issue", "body": "Something", "labels": []}
        issue2 = {"number": 2, "title": "Null pointer", "body": "NPE crash", "labels": []}

        score1 = analyzer._score_issue(issue1)
        score2 = analyzer._score_issue(issue2)

        assert score2.score > score1.score
