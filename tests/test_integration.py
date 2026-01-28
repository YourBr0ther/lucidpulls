"""Integration tests using real filesystem and database operations."""

import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytz

from src.analyzers.base import MAX_CHARS_FOR_LLM, FixSuggestion
from src.analyzers.code_analyzer import CodeAnalyzer
from src.database.history import ReviewHistory
from src.scheduler import DeadlineEnforcer


class TestCodeAnalyzerIntegration:
    """Integration tests using real filesystem operations."""

    def test_get_code_files_real_directory(self, tmp_path):
        """Test file discovery on a real directory tree."""
        # Create real files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "src" / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "README.md").write_text("# Project\n")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("module.exports = {}\n")

        analyzer = CodeAnalyzer(Mock())
        files = analyzer._get_code_files(tmp_path)

        paths = [str(p) for p, _ in files]
        assert any("main.py" in p for p in paths)
        assert any("utils.py" in p for p in paths)
        # node_modules should be skipped
        assert not any("node_modules" in p for p in paths)
        # .md files should be skipped (not in default extensions)
        assert not any("README.md" in p for p in paths)

    def test_format_code_for_llm_truncation(self, tmp_path):
        """Test that files exceeding MAX_CHARS_FOR_LLM are truncated."""
        # Create a file larger than the limit
        big_content = "x = 1\n" * (MAX_CHARS_FOR_LLM // 6 + 1000)
        (tmp_path / "big.py").write_text(big_content)

        analyzer = CodeAnalyzer(Mock())
        files = analyzer._get_code_files(tmp_path)
        result = analyzer._format_code_for_llm(files)

        assert len(result) <= MAX_CHARS_FOR_LLM + 200  # allow small overhead
        assert "[truncated]" in result

    def test_parse_and_apply_fix_end_to_end(self, tmp_path):
        """Test parsing realistic LLM JSON then applying to a real file."""
        # Create a real source file
        source = "def process(data):\n    return data.value\n"
        (tmp_path / "handler.py").write_text(source)

        llm_response = json.dumps({
            "found_bug": True,
            "file_path": "handler.py",
            "bug_description": "Missing null check on data parameter",
            "fix_description": "Added None check before accessing .value",
            "original_code": "return data.value",
            "fixed_code": "return data.value if data is not None else None",
            "pr_title": "Fix null pointer in handler",
            "pr_body": "Added null check for data parameter",
            "confidence": "high",
        })

        analyzer = CodeAnalyzer(Mock())
        fix = analyzer._parse_llm_response(llm_response)
        assert fix is not None

        applied = analyzer.apply_fix(tmp_path, fix)
        assert applied is True

        new_content = (tmp_path / "handler.py").read_text()
        assert "data.value if data is not None else None" in new_content

    def test_parse_llm_response_oversized(self):
        """Test that oversized LLM responses are truncated before parsing."""
        analyzer = CodeAnalyzer(Mock())

        # Create a response larger than the limit with valid JSON at the start
        valid_json = json.dumps({"found_bug": False})
        padding = "x" * (analyzer.MAX_LLM_RESPONSE_SIZE + 1000)
        oversized = valid_json + padding

        # Should not raise, should truncate and still parse
        result = analyzer._parse_llm_response(oversized)
        assert result is None  # found_bug is False

    def test_apply_fix_path_traversal(self, tmp_path):
        """Test that path traversal is blocked with real paths."""
        (tmp_path / "safe.py").write_text("x = 1\n")

        analyzer = CodeAnalyzer(Mock())

        # Test traversal via file_path in FixSuggestion
        fix = FixSuggestion(
            file_path="../../etc/passwd",
            bug_description="Bug",
            fix_description="Fix",
            original_code="x = 1",
            fixed_code="x = 2",
            pr_title="Title",
            pr_body="Body",
            confidence="high",
        )
        assert analyzer.apply_fix(tmp_path, fix) is False

    def test_apply_fix_null_bytes_in_path(self, tmp_path):
        """Test that null byte paths are rejected at parse time."""
        analyzer = CodeAnalyzer(Mock())

        response = json.dumps({
            "found_bug": True,
            "file_path": "src/main\x00.py",
            "bug_description": "Bug",
            "fix_description": "Fix",
            "original_code": "old",
            "fixed_code": "new",
            "pr_title": "Title",
            "pr_body": "Body",
            "confidence": "high",
        })

        fix = analyzer._parse_llm_response(response)
        assert fix is None  # Null byte should cause rejection

    def test_parse_llm_response_absolute_path_rejected(self):
        """Test that absolute paths from LLM are rejected."""
        analyzer = CodeAnalyzer(Mock())

        response = json.dumps({
            "found_bug": True,
            "file_path": "/etc/passwd",
            "bug_description": "Bug",
            "fix_description": "Fix",
            "original_code": "old",
            "fixed_code": "new",
            "pr_title": "Title",
            "pr_body": "Body",
            "confidence": "high",
        })

        fix = analyzer._parse_llm_response(response)
        assert fix is None

    def test_parse_llm_response_dotdot_rejected(self):
        """Test that .. traversal in file_path from LLM is rejected."""
        analyzer = CodeAnalyzer(Mock())

        response = json.dumps({
            "found_bug": True,
            "file_path": "src/../../../etc/passwd",
            "bug_description": "Bug",
            "fix_description": "Fix",
            "original_code": "old",
            "fixed_code": "new",
            "pr_title": "Title",
            "pr_body": "Body",
            "confidence": "high",
        })

        fix = analyzer._parse_llm_response(response)
        assert fix is None

    def test_validate_python_syntax_real_file(self, tmp_path):
        """Test Python syntax validation with real files."""
        analyzer = CodeAnalyzer(Mock())

        # Valid Python
        valid_file = tmp_path / "valid.py"
        valid_file.write_text("def foo():\n    return 42\n")
        assert analyzer._validate_python_syntax(valid_file) is True

        # Invalid Python
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("def foo(\n")
        assert analyzer._validate_python_syntax(invalid_file) is False


class TestDeadlineEnforcerIntegration:
    """Integration tests for deadline enforcement with real time."""

    def test_deadline_enforcement_real_time(self):
        """Test deadline with a very short window using real time."""
        # Create a deadline enforcer with a deadline that's already passed
        # by using a time that's before now
        tz = pytz.timezone("UTC")
        now = datetime.now(tz)

        # Set deadline to current hour/minute (already reached)
        deadline_str = now.strftime("%H:%M")
        enforcer = DeadlineEnforcer(deadline_time=deadline_str, timezone="UTC")
        enforcer.mark_review_started()

        # Since the deadline is at the same time as start, it should wrap to tomorrow
        # so it should NOT be past deadline yet
        assert enforcer.is_past_deadline() is False

        # Now test with a deadline that was set to a time before the start
        # by manually setting _review_started_at to 1 second before now
        enforcer._review_started_at = now - timedelta(seconds=2)

        # The deadline is at now's time, started 2 seconds ago, so if
        # the deadline time matches now, it should be past
        remaining = enforcer.time_remaining()
        # The deadline wraps to tomorrow since it's <= start time
        assert remaining is not None


class TestReviewHistoryIntegration:
    """Integration tests for ReviewHistory with real SQLite."""

    def test_full_lifecycle(self, tmp_path):
        """Test start_run -> record_pr -> complete_run -> build_report."""
        db_path = str(tmp_path / "test.db")
        history = ReviewHistory(db_path=db_path)

        try:
            # Start a run
            run_id = history.start_run()
            assert run_id is not None

            # Record a successful PR
            history.record_pr(
                run_id,
                repo_name="owner/repo",
                pr_number=42,
                pr_url="https://github.com/owner/repo/pull/42",
                pr_title="Fix bug",
                success=True,
            )

            # Record a failed repo
            history.record_pr(
                run_id,
                repo_name="owner/other-repo",
                success=False,
                error="No actionable issues",
            )

            # Complete the run
            history.complete_run(run_id, repos_reviewed=2, prs_created=1)

            # Build report
            report = history.build_report(run_id)
            assert report is not None
            assert report.repos_reviewed == 2
            assert report.prs_created == 1
            assert len(report.prs) == 2

            # Verify PR details
            success_prs = [p for p in report.prs if p.success]
            assert len(success_prs) == 1
            assert success_prs[0].pr_number == 42

            # Verify latest run
            latest = history.get_latest_run()
            assert latest is not None
            assert latest.id == run_id
            assert latest.status == "completed"
        finally:
            history.close()

    def test_record_pr_error_does_not_crash(self, tmp_path):
        """Test that database errors in record_pr are caught gracefully."""
        from sqlalchemy import text

        db_path = str(tmp_path / "test2.db")
        history = ReviewHistory(db_path=db_path)

        try:
            run_id = history.start_run()

            # Drop the pr_records table to cause an error in record_pr
            with history.engine.connect() as conn:
                conn.execute(text("DROP TABLE pr_records"))
                conn.commit()

            # record_pr should catch the error internally and not raise
            history.record_pr(
                run_id,
                repo_name="owner/repo",
                success=False,
                error="test",
            )

            # Drop review_runs table to cause an error in complete_run
            with history.engine.connect() as conn:
                conn.execute(text("DROP TABLE review_runs"))
                conn.commit()

            # complete_run should catch the error internally and not raise
            history.complete_run(run_id, repos_reviewed=1, prs_created=0)
        finally:
            history.close()
