"""Tests for database operations."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.database.models import Base, ReviewRun, PRRecord
from src.database.history import ReviewHistory


class TestReviewHistory:
    """Tests for ReviewHistory."""

    def test_init_creates_tables(self):
        """Test initialization creates tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            # Verify file was created
            assert Path(db_path).exists()

    def test_start_run(self):
        """Test starting a review run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()

            assert run.id is not None
            assert run.status == "running"
            assert run.started_at is not None

    def test_complete_run(self):
        """Test completing a review run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            history.complete_run(run.id, repos_reviewed=3, prs_created=2)

            # Refresh from DB
            updated_run = history.get_run(run.id)
            assert updated_run.status == "completed"
            assert updated_run.repos_reviewed == 3
            assert updated_run.prs_created == 2
            assert updated_run.completed_at is not None

    def test_complete_run_with_error(self):
        """Test completing a run with error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            history.complete_run(run.id, repos_reviewed=1, prs_created=0, error="Failed")

            updated_run = history.get_run(run.id)
            assert updated_run.status == "failed"
            assert updated_run.error == "Failed"

    def test_record_pr_success(self):
        """Test recording a successful PR."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            pr = history.record_pr(
                run_id=run.id,
                repo_name="owner/repo",
                pr_number=42,
                pr_url="https://github.com/owner/repo/pull/42",
                pr_title="Fix bug",
                success=True,
            )

            assert pr.id is not None
            assert pr.repo_name == "owner/repo"
            assert pr.pr_number == 42
            assert pr.success is True

    def test_record_pr_failure(self):
        """Test recording a failed PR attempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            pr = history.record_pr(
                run_id=run.id,
                repo_name="owner/repo",
                success=False,
                error="No fixes found",
            )

            assert pr.success is False
            assert pr.error == "No fixes found"

    def test_get_run_prs(self):
        """Test getting PRs for a run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            history.record_pr(run.id, "owner/repo1", pr_number=1, success=True)
            history.record_pr(run.id, "owner/repo2", pr_number=2, success=True)
            history.record_pr(run.id, "owner/repo3", success=False)

            prs = history.get_run_prs(run.id)

            assert len(prs) == 3

    def test_get_latest_run(self):
        """Test getting the latest run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run1 = history.start_run()
            run2 = history.start_run()

            latest = history.get_latest_run()

            assert latest.id == run2.id

    def test_build_report(self):
        """Test building a review report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run = history.start_run()
            history.record_pr(
                run.id,
                "owner/repo1",
                pr_number=42,
                pr_url="https://github.com/owner/repo1/pull/42",
                pr_title="Fix bug",
                success=True,
            )
            history.record_pr(
                run.id,
                "owner/repo2",
                success=False,
                error="No fixes found",
            )
            history.complete_run(run.id, repos_reviewed=2, prs_created=1)

            report = history.build_report(run.id)

            assert report is not None
            assert report.repos_reviewed == 2
            assert report.prs_created == 1
            assert len(report.prs) == 2

            # Check PR summaries
            successful = [p for p in report.prs if p.success]
            assert len(successful) == 1
            assert successful[0].repo_name == "owner/repo1"
            assert successful[0].pr_number == 42

    def test_build_report_nonexistent(self):
        """Test building report for nonexistent run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            report = history.build_report(999)

            assert report is None

    def test_get_recent_runs(self):
        """Test getting recent runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            for _ in range(5):
                history.start_run()

            runs = history.get_recent_runs(limit=3)

            assert len(runs) == 3
