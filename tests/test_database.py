"""Tests for database operations."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

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

            run_id = history.start_run()

            assert run_id is not None
            assert isinstance(run_id, int)

            # Verify in DB
            run = history.get_run(run_id)
            assert run.status == "running"
            assert run.started_at is not None

    def test_complete_run(self):
        """Test completing a review run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.complete_run(run_id, repos_reviewed=3, prs_created=2)

            # Refresh from DB
            updated_run = history.get_run(run_id)
            assert updated_run.status == "completed"
            assert updated_run.repos_reviewed == 3
            assert updated_run.prs_created == 2
            assert updated_run.completed_at is not None

    def test_complete_run_with_error(self):
        """Test completing a run with error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.complete_run(run_id, repos_reviewed=1, prs_created=0, error="Failed")

            updated_run = history.get_run(run_id)
            assert updated_run.status == "failed"
            assert updated_run.error == "Failed"

    def test_record_pr_success(self):
        """Test recording a successful PR."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(
                run_id=run_id,
                repo_name="owner/repo",
                pr_number=42,
                pr_url="https://github.com/owner/repo/pull/42",
                pr_title="Fix bug",
                success=True,
            )

            prs = history.get_run_prs(run_id)
            assert len(prs) == 1
            assert prs[0].repo_name == "owner/repo"
            assert prs[0].pr_number == 42
            assert prs[0].success is True

    def test_record_pr_failure(self):
        """Test recording a failed PR attempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(
                run_id=run_id,
                repo_name="owner/repo",
                success=False,
                error="No fixes found",
            )

            prs = history.get_run_prs(run_id)
            assert len(prs) == 1
            assert prs[0].success is False
            assert prs[0].error == "No fixes found"

    def test_get_run_prs(self):
        """Test getting PRs for a run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(run_id, "owner/repo1", pr_number=1, success=True)
            history.record_pr(run_id, "owner/repo2", pr_number=2, success=True)
            history.record_pr(run_id, "owner/repo3", success=False)

            prs = history.get_run_prs(run_id)

            assert len(prs) == 3

    def test_get_latest_run(self):
        """Test getting the latest run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id1 = history.start_run()
            run_id2 = history.start_run()

            latest = history.get_latest_run()

            assert latest.id == run_id2

    def test_record_pr_with_bug_description(self):
        """Test recording a PR with bug_description round-trips through DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(
                run_id,
                "owner/repo",
                pr_number=10,
                pr_url="https://github.com/owner/repo/pull/10",
                pr_title="Fix null check",
                success=True,
                bug_description="Missing null check causes crash on empty input",
            )

            prs = history.get_run_prs(run_id)
            assert len(prs) == 1
            assert prs[0].bug_description == "Missing null check causes crash on empty input"

    def test_build_report_includes_bug_description(self):
        """Test that build_report passes bug_description to PRSummary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(
                run_id,
                "owner/repo1",
                pr_number=42,
                pr_url="https://github.com/owner/repo1/pull/42",
                pr_title="Fix bug",
                success=True,
                bug_description="Off-by-one in loop bounds",
            )
            history.complete_run(run_id, repos_reviewed=1, prs_created=1)

            report = history.build_report(run_id)
            assert report.prs[0].bug_description == "Off-by-one in loop bounds"

    def test_record_pr_without_bug_description(self):
        """Test that bug_description defaults to None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(run_id, "owner/repo", success=False, error="No fixes")

            prs = history.get_run_prs(run_id)
            assert prs[0].bug_description is None

    def test_build_report(self):
        """Test building a review report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            run_id = history.start_run()
            history.record_pr(
                run_id,
                "owner/repo1",
                pr_number=42,
                pr_url="https://github.com/owner/repo1/pull/42",
                pr_title="Fix bug",
                success=True,
            )
            history.record_pr(
                run_id,
                "owner/repo2",
                success=False,
                error="No fixes found",
            )
            history.complete_run(run_id, repos_reviewed=2, prs_created=1)

            report = history.build_report(run_id)

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

    def test_close_disposes_engine(self):
        """Test close method disposes the database engine."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history = ReviewHistory(db_path=f"{tmpdir}/test.db")

            # Should not raise
            history.close()

            # Engine should be disposed (calling again should be safe)
            history.close()


class TestAlembicMigrations:
    """Tests for Alembic migration integration."""

    def test_fresh_db_runs_migrations(self):
        """Test that a fresh database gets all tables via migrations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/fresh.db"
            history = ReviewHistory(db_path=db_path)

            inspector = inspect(history.engine)
            tables = inspector.get_table_names()
            assert "review_runs" in tables
            assert "pr_records" in tables
            assert "alembic_version" in tables
            history.close()

    def test_existing_db_gets_stamped(self):
        """Test that an existing DB without alembic_version gets stamped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/existing.db"
            # Create tables the old way (without Alembic)
            engine = create_engine(f"sqlite:///{db_path}")
            Base.metadata.create_all(engine)
            engine.dispose()

            # Now open with ReviewHistory — should stamp and upgrade
            history = ReviewHistory(db_path=db_path)

            inspector = inspect(history.engine)
            tables = inspector.get_table_names()
            assert "alembic_version" in tables

            # Verify stamp is at head (0003 after bug_description migration)
            with history.engine.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.scalar()
                assert version == "0003"
            history.close()

    def test_migration_idempotent(self):
        """Test that running migrations twice is safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/idem.db"
            history1 = ReviewHistory(db_path=db_path)
            history1.start_run()
            history1.close()

            # Open again — should not fail
            history2 = ReviewHistory(db_path=db_path)
            run_id = history2.start_run()
            assert run_id is not None
            history2.close()
