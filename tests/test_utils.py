"""Tests for utility functions."""

import time
from unittest.mock import patch

import pytest

from src.utils import retry, sanitize_branch_name, parse_time_string


class TestRetryDecorator:
    """Tests for the retry decorator."""

    def test_succeeds_first_attempt(self):
        """Test function that succeeds on first try."""
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_succeeds_after_retries(self):
        """Test function that fails then succeeds."""
        call_count = 0

        @retry(max_attempts=3, delay=0.01, backoff=1.0)
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        result = fail_then_succeed()
        assert result == "ok"
        assert call_count == 3

    def test_exhausts_max_attempts(self):
        """Test raises after all attempts exhausted."""
        call_count = 0

        @retry(max_attempts=2, delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            always_fail()
        assert call_count == 2

    def test_only_catches_specified_exceptions(self):
        """Test non-matching exceptions propagate immediately."""
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def raise_type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError, match="wrong type"):
            raise_type_error()
        assert call_count == 1  # No retries for TypeError

    def test_exponential_backoff(self):
        """Test that backoff increases delay between attempts."""
        @retry(max_attempts=3, delay=0.05, backoff=2.0)
        def always_fail():
            raise ValueError("fail")

        start = time.monotonic()
        with pytest.raises(ValueError):
            always_fail()
        elapsed = time.monotonic() - start

        # Should wait ~0.05 + ~0.10 = ~0.15 seconds minimum
        assert elapsed >= 0.1


class TestSanitizeBranchName:
    """Tests for sanitize_branch_name."""

    def test_basic_sanitization(self):
        """Test basic character replacement."""
        assert sanitize_branch_name("src/main.py") == "src-main.py"

    def test_removes_special_characters(self):
        """Test special characters are removed."""
        assert sanitize_branch_name("file@name#test") == "filenametest"

    def test_removes_spaces(self):
        """Test spaces are converted to dashes."""
        result = sanitize_branch_name("my file name")
        assert " " not in result

    def test_no_consecutive_dashes(self):
        """Test consecutive dashes are collapsed."""
        result = sanitize_branch_name("a///b")
        assert "---" not in result

    def test_no_leading_trailing_dashes(self):
        """Test no leading or trailing dashes."""
        result = sanitize_branch_name("/path/")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_length_limit(self):
        """Test output is capped at 50 characters."""
        long_name = "a" * 100
        result = sanitize_branch_name(long_name)
        assert len(result) <= 50

    def test_path_traversal_characters(self):
        """Test path traversal characters are sanitized."""
        result = sanitize_branch_name("../../etc/passwd")
        assert ".." not in result or "/" not in result
        assert result  # Not empty

    def test_backslash_replacement(self):
        """Test backslashes are replaced."""
        result = sanitize_branch_name("src\\main.py")
        assert "\\" not in result


class TestParseTimeString:
    """Tests for parse_time_string."""

    def test_valid_time(self):
        """Test parsing valid time strings."""
        assert parse_time_string("02:00") == (2, 0)
        assert parse_time_string("23:59") == (23, 59)
        assert parse_time_string("00:00") == (0, 0)

    def test_invalid_format_no_colon(self):
        """Test invalid format without colon."""
        with pytest.raises(ValueError):
            parse_time_string("0200")

    def test_invalid_format_empty(self):
        """Test empty string."""
        with pytest.raises(ValueError):
            parse_time_string("")

    def test_invalid_hour(self):
        """Test hour out of range."""
        with pytest.raises(ValueError):
            parse_time_string("25:00")

    def test_invalid_minute(self):
        """Test minute out of range."""
        with pytest.raises(ValueError):
            parse_time_string("12:60")

    def test_non_numeric(self):
        """Test non-numeric values."""
        with pytest.raises(ValueError):
            parse_time_string("ab:cd")
