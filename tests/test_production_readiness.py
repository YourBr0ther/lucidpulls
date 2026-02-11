"""Tests for production readiness features: backups, WAL, log correlation, git retries,
notification retry, and database indexes."""

import json
import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from src.database.history import ReviewHistory

# ---------------------------------------------------------------------------
# 1. Database Indexes
# ---------------------------------------------------------------------------

class TestDatabaseIndexes:
    """Tests for database index migration."""

    def test_indexes_exist_after_migration(self):
        """Verify all 5 indexes are created by the migration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            inspector = inspect(history.engine)

            pr_indexes = {idx["name"] for idx in inspector.get_indexes("pr_records")}
            run_indexes = {idx["name"] for idx in inspector.get_indexes("review_runs")}

            assert "ix_pr_records_review_run_id" in pr_indexes
            assert "ix_pr_records_repo_name" in pr_indexes
            assert "ix_pr_records_created_at" in pr_indexes
            assert "ix_review_runs_started_at" in run_indexes
            assert "ix_review_runs_status" in run_indexes

            history.close()

    def test_migration_version_at_0004(self):
        """Verify DB is at migration revision 0004."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            with history.engine.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.scalar()
                assert version == "0004"

            history.close()


# ---------------------------------------------------------------------------
# 2. Database Backups + WAL Mode
# ---------------------------------------------------------------------------

class TestWALMode:
    """Tests for WAL journal mode."""

    def test_wal_mode_enabled(self):
        """Verify WAL mode is set on the database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            with history.engine.connect() as conn:
                result = conn.execute(text("PRAGMA journal_mode"))
                mode = result.scalar()
                assert mode == "wal"

            history.close()


class TestDatabaseBackup:
    """Tests for database backup functionality."""

    def test_backup_creates_file(self):
        """Test that backup creates a valid SQLite file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)
            history.start_run()

            backup_path = history.backup_database(backup_count=3)

            assert backup_path is not None
            assert Path(backup_path).exists()

            # Verify the backup is a valid SQLite database
            conn = sqlite3.connect(backup_path)
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM review_runs")
            count = cursor.fetchone()[0]
            assert count == 1
            conn.close()

            history.close()

    def test_backup_rotation(self):
        """Test that old backups are deleted when exceeding count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            backup_dir = Path(db_path).parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Pre-create 4 old backups with known names
            old_paths = []
            for i in range(4):
                old = backup_dir / f"lucidpulls_20260101_00000{i}.db"
                old.write_bytes(b"")
                old_paths.append(old)

            # Now create a real backup with count=3
            history.backup_database(backup_count=3)

            remaining = sorted(backup_dir.glob("lucidpulls_*.db"))
            assert len(remaining) == 3

            # The two oldest stubs should have been deleted
            assert not old_paths[0].exists()
            assert not old_paths[1].exists()

            history.close()

    def test_backup_returns_none_on_failure(self):
        """Test that backup returns None on failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"
            history = ReviewHistory(db_path=db_path)

            # Point db_path to a nonexistent file to trigger failure
            history.db_path = "/nonexistent/path/test.db"
            result = history.backup_database()

            assert result is None

            history.close()


# ---------------------------------------------------------------------------
# 3. Log Correlation (Run IDs)
# ---------------------------------------------------------------------------

class TestLogCorrelation:
    """Tests for run ID log correlation."""

    def test_run_id_filter_injects_default(self):
        """Test RunIDFilter sets run_id to '-' when no context."""
        from src import RunIDFilter

        f = RunIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        f.filter(record)
        assert record.run_id == "-"

    def test_run_id_filter_injects_value(self):
        """Test RunIDFilter picks up value from contextvars."""
        from src import RunIDFilter, current_run_id

        token = current_run_id.set("42")
        try:
            f = RunIDFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test", args=(), exc_info=None,
            )
            f.filter(record)
            assert record.run_id == "42"
        finally:
            current_run_id.reset(token)

    def test_text_formatter_includes_run_id(self):
        """Test text format includes run=<id>."""
        from src import current_run_id, setup_logging

        token = current_run_id.set("99")
        try:
            logger = setup_logging(level="INFO", log_format="text")
            handler = logger.handlers[0]
            record = logging.LogRecord(
                name="lucidpulls.test", level=logging.INFO, pathname="", lineno=0,
                msg="hello", args=(), exc_info=None,
            )
            handler.filters[0].filter(record)
            output = handler.formatter.format(record)
            assert "run=99" in output
        finally:
            current_run_id.reset(token)

    def test_json_formatter_includes_run_id(self):
        """Test JSON format includes run_id key."""
        from src import current_run_id, setup_logging

        token = current_run_id.set("77")
        try:
            logger = setup_logging(level="INFO", log_format="json")
            handler = logger.handlers[0]
            record = logging.LogRecord(
                name="lucidpulls.test", level=logging.INFO, pathname="", lineno=0,
                msg="hello", args=(), exc_info=None,
            )
            handler.filters[0].filter(record)
            output = handler.formatter.format(record)
            data = json.loads(output)
            assert data["run_id"] == "77"
        finally:
            current_run_id.reset(token)

    def test_run_review_sets_and_clears_run_id(self):
        """Test that run_review sets and then clears the run ID context."""
        from src import current_run_id

        @patch("src.main.get_notifier")
        @patch("src.main.get_llm")
        @patch("src.main.ReviewHistory")
        @patch("src.main.Github")
        @patch("src.main.Auth")
        def _test(mock_auth, mock_github, mock_history, mock_get_llm, mock_get_notifier):
            from src.main import LucidPulls

            settings = Mock()
            settings.repos = ""
            settings.repo_list = []
            settings.github_token = "test-token"
            settings.github_username = "testuser"
            settings.github_email = "test@example.com"
            settings.ssh_key_path = ""
            settings.clone_dir = "/tmp/lucidpulls/repos"
            settings.max_clone_disk_mb = 5000
            settings.max_workers = 1
            settings.llm_provider = "ollama"
            settings.notification_channel = "discord"
            settings.schedule_start = "02:00"
            settings.schedule_deadline = "06:00"
            settings.report_delivery = "07:00"
            settings.timezone = "America/New_York"
            settings.log_level = "INFO"
            settings.log_format = "text"
            settings.dry_run = False
            settings.db_backup_enabled = False
            settings.db_backup_count = 7
            settings.get_llm_config.return_value = {"host": "http://localhost:11434", "model": "codellama"}
            settings.get_notification_config.return_value = {"webhook_url": ""}

            mock_history.return_value.start_run.return_value = 42

            agent = LucidPulls(settings)
            agent.run_review()

            # After run_review, current_run_id should be reset
            assert current_run_id.get("-") == "-"

        _test()


# ---------------------------------------------------------------------------
# 4. Git Operation Retries
# ---------------------------------------------------------------------------

class TestGitRetries:
    """Tests for git push and clone retries."""

    def test_push_retries_on_transient_error(self):
        """Test push_branch retries on GitCommandError."""
        from git.remote import PushInfo

        from git import GitCommandError
        from src.git.repo_manager import RepoInfo, RepoManager
        mock_origin = Mock()
        # Fail twice, succeed on third (with valid push info)
        mock_push_info = Mock(spec=PushInfo)
        mock_push_info.flags = 0  # No error flags
        mock_push_info.ERROR = PushInfo.ERROR
        mock_origin.push.side_effect = [
            GitCommandError("push", "network error"),
            GitCommandError("push", "network error"),
            [mock_push_info],
        ]
        mock_repo = Mock()
        mock_repo.remotes.origin = mock_origin

        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = RepoManager(
            github=Mock(), rate_limiter=Mock(),
            username="test", email="test@test.com",
        )

        with patch("src.utils.time.sleep"):  # skip actual delay
            result = manager.push_branch(repo_info, "feature")

        assert result is True
        assert mock_origin.push.call_count == 3

    def test_push_fails_after_max_retries(self):
        """Test push_branch returns False after exhausting retries."""
        from git import GitCommandError
        from src.git.repo_manager import RepoInfo, RepoManager

        mock_origin = Mock()
        mock_origin.push.side_effect = GitCommandError("push", "network error")
        mock_repo = Mock()
        mock_repo.remotes.origin = mock_origin

        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = RepoManager(
            github=Mock(), rate_limiter=Mock(),
            username="test", email="test@test.com",
        )

        with patch("src.utils.time.sleep"):
            result = manager.push_branch(repo_info, "feature")

        assert result is False
        assert mock_origin.push.call_count == 3

    def test_clone_retries_on_transient_error(self):
        """Test _clone_repo retries on GitCommandError."""
        from git import GitCommandError
        from src.git.repo_manager import RepoManager

        manager = RepoManager(
            github=Mock(), rate_limiter=Mock(),
            username="test", email="test@test.com",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "owner" / "repo"

            with patch("src.git.repo_manager.Repo") as mock_repo_class, \
                 patch("src.utils.time.sleep"):
                mock_repo_class.clone_from.side_effect = [
                    GitCommandError("clone", "timeout"),
                    GitCommandError("clone", "timeout"),
                    Mock(),  # success on 3rd try
                ]
                result = manager._clone_repo("git@github.com:owner/repo.git", local_path)

            assert result is not None
            assert mock_repo_class.clone_from.call_count == 3

    def test_clone_fails_after_max_retries(self):
        """Test _clone_repo returns None after exhausting retries."""
        from git import GitCommandError
        from src.git.repo_manager import RepoManager

        manager = RepoManager(
            github=Mock(), rate_limiter=Mock(),
            username="test", email="test@test.com",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "owner" / "repo"

            with patch("src.git.repo_manager.Repo") as mock_repo_class, \
                 patch("src.utils.time.sleep"):
                mock_repo_class.clone_from.side_effect = GitCommandError("clone", "timeout")
                result = manager._clone_repo("git@github.com:owner/repo.git", local_path)

            assert result is None
            assert mock_repo_class.clone_from.call_count == 3


# ---------------------------------------------------------------------------
# 5. Notification Retry
# ---------------------------------------------------------------------------

class TestNotificationRetry:
    """Tests for notification retry in send_report."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_notification_retries_on_failure(self, mock_auth, mock_github,
                                              mock_history, mock_get_llm, mock_get_notifier):
        """Test that send_report retries when notification fails then succeeds."""
        from src.main import LucidPulls

        settings = Mock()
        settings.repos = "owner/repo"
        settings.repo_list = ["owner/repo"]
        settings.github_token = "test-token"
        settings.github_username = "testuser"
        settings.github_email = "test@example.com"
        settings.ssh_key_path = ""
        settings.clone_dir = "/tmp/lucidpulls/repos"
        settings.max_clone_disk_mb = 5000
        settings.max_workers = 1
        settings.llm_provider = "ollama"
        settings.notification_channel = "discord"
        settings.schedule_start = "02:00"
        settings.schedule_deadline = "06:00"
        settings.report_delivery = "07:00"
        settings.timezone = "America/New_York"
        settings.log_level = "INFO"
        settings.log_format = "text"
        settings.dry_run = False
        settings.db_backup_enabled = False
        settings.db_backup_count = 7
        settings.get_llm_config.return_value = {"host": "http://localhost:11434", "model": "codellama"}
        settings.get_notification_config.return_value = {"webhook_url": "https://example.com"}

        # Set up latest run
        mock_run = Mock()
        mock_run.id = 1
        mock_run.status = "completed"
        mock_run.started_at = datetime.utcnow()  # naive UTC, matching DB convention
        mock_history.return_value.get_latest_run.return_value = mock_run
        mock_history.return_value.build_report.return_value = Mock()

        # Fail first, succeed second
        fail_result = Mock(success=False, error="Timeout")
        success_result = Mock(success=True)
        mock_get_notifier.return_value.send_report.side_effect = [fail_result, success_result]

        agent = LucidPulls(settings)
        agent._shutdown_requested = Mock()
        agent._shutdown_requested.wait.return_value = False  # Not interrupted
        agent.send_report()

        assert mock_get_notifier.return_value.send_report.call_count == 2

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_notification_gives_up_after_max_attempts(self, mock_auth, mock_github,
                                                       mock_history, mock_get_llm, mock_get_notifier):
        """Test that send_report stops after 3 failed attempts."""
        from src.main import LucidPulls

        settings = Mock()
        settings.repos = "owner/repo"
        settings.repo_list = ["owner/repo"]
        settings.github_token = "test-token"
        settings.github_username = "testuser"
        settings.github_email = "test@example.com"
        settings.ssh_key_path = ""
        settings.clone_dir = "/tmp/lucidpulls/repos"
        settings.max_clone_disk_mb = 5000
        settings.max_workers = 1
        settings.llm_provider = "ollama"
        settings.notification_channel = "discord"
        settings.schedule_start = "02:00"
        settings.schedule_deadline = "06:00"
        settings.report_delivery = "07:00"
        settings.timezone = "America/New_York"
        settings.log_level = "INFO"
        settings.log_format = "text"
        settings.dry_run = False
        settings.db_backup_enabled = False
        settings.db_backup_count = 7
        settings.get_llm_config.return_value = {"host": "http://localhost:11434", "model": "codellama"}
        settings.get_notification_config.return_value = {"webhook_url": "https://example.com"}

        mock_run = Mock()
        mock_run.id = 1
        mock_run.status = "completed"
        mock_run.started_at = datetime.utcnow()  # naive UTC, matching DB convention
        mock_history.return_value.get_latest_run.return_value = mock_run
        mock_history.return_value.build_report.return_value = Mock()

        fail_result = Mock(success=False, error="Timeout")
        mock_get_notifier.return_value.send_report.return_value = fail_result

        agent = LucidPulls(settings)
        agent._shutdown_requested = Mock()
        agent._shutdown_requested.wait.return_value = False  # Not interrupted
        agent.send_report()

        assert mock_get_notifier.return_value.send_report.call_count == 3
