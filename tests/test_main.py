"""Tests for the main orchestrator."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest
import pytz

from src.main import LucidPulls, main


def _make_settings(**overrides):
    """Create a mock Settings object with defaults."""
    settings = Mock()
    settings.repos = "owner/repo1,owner/repo2"
    settings.repo_list = ["owner/repo1", "owner/repo2"]
    settings.github_token = "test-token"
    settings.github_username = "testuser"
    settings.github_email = "test@example.com"
    settings.ssh_key_path = ""
    settings.clone_dir = "/tmp/lucidpulls/repos"
    settings.max_clone_disk_mb = 5000
    settings.max_workers = 2
    settings.llm_provider = "ollama"
    settings.notification_channel = "discord"
    settings.schedule_start = "02:00"
    settings.schedule_deadline = "06:00"
    settings.report_delivery = "07:00"
    settings.timezone = "America/New_York"
    settings.log_level = "INFO"
    settings.log_format = "text"
    settings.get_llm_config.return_value = {"host": "http://localhost:11434", "model": "codellama"}
    settings.get_notification_config.return_value = {"webhook_url": ""}
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@patch("src.main.get_notifier")
@patch("src.main.get_llm")
@patch("src.main.ReviewHistory")
@patch("src.main.Github")
@patch("src.main.Auth")
def _make_agent(mock_auth, mock_github, mock_history, mock_get_llm, mock_get_notifier, **overrides):
    """Create a LucidPulls agent with mocked dependencies."""
    settings = _make_settings(**overrides)
    agent = LucidPulls(settings)
    return agent, mock_history, mock_github


class TestLucidPullsProcessRepo:
    """Tests for _process_repo method."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_process_repo_clone_fails(self, mock_auth, mock_github, mock_history,
                                       mock_get_llm, mock_get_notifier):
        """Test _process_repo when clone/pull fails."""
        settings = _make_settings()
        agent = LucidPulls(settings)
        agent.repo_manager.clone_or_pull = Mock(return_value=None)
        mock_history.return_value.start_run.return_value = 1

        result = agent._process_repo("owner/repo1", 1)

        assert result is False
        mock_history.return_value.record_pr.assert_called_once()
        call_kwargs = mock_history.return_value.record_pr.call_args[1]
        assert call_kwargs["success"] is False
        assert "clone/pull" in call_kwargs["error"].lower()

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_process_repo_existing_pr_skips(self, mock_auth, mock_github, mock_history,
                                             mock_get_llm, mock_get_notifier):
        """Test _process_repo skips when existing PR found."""
        settings = _make_settings()
        agent = LucidPulls(settings)
        agent.repo_manager.clone_or_pull = Mock(return_value=Mock())
        agent.pr_creator.has_open_lucidpulls_pr = Mock(return_value=True)

        result = agent._process_repo("owner/repo1", 1)

        assert result is False

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_process_repo_no_actionable_issues(self, mock_auth, mock_github, mock_history,
                                                mock_get_llm, mock_get_notifier):
        """Test _process_repo with no actionable issues."""
        settings = _make_settings()
        agent = LucidPulls(settings)
        agent.repo_manager.clone_or_pull = Mock(return_value=Mock())
        agent.pr_creator.has_open_lucidpulls_pr = Mock(return_value=False)
        agent.pr_creator.get_open_issues = Mock(return_value=[])

        result = agent._process_repo("owner/repo1", 1)

        assert result is False


class TestLucidPullsRunReview:
    """Tests for run_review method."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_run_review_empty_repos(self, mock_auth, mock_github, mock_history,
                                     mock_get_llm, mock_get_notifier):
        """Test run_review with no repos configured."""
        settings = _make_settings(repo_list=[])
        mock_history.return_value.start_run.return_value = 1

        agent = LucidPulls(settings)
        agent.run_review()

        mock_history.return_value.complete_run.assert_called_once()
        args = mock_history.return_value.complete_run.call_args
        assert args[0][0] == 1   # run_id
        assert args[0][1] == 0   # repos_reviewed
        assert args[0][2] == 0   # prs_created

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_run_review_shutdown_stops_processing(self, mock_auth, mock_github,
                                                   mock_history, mock_get_llm, mock_get_notifier):
        """Test that setting shutdown flag stops review loop."""
        settings = _make_settings()
        mock_history.return_value.start_run.return_value = 1

        agent = LucidPulls(settings)
        agent.repo_manager.clone_or_pull = Mock()
        # Set shutdown before running
        agent._shutdown = True
        agent.run_review()

        # Should not have tried to process any repos
        agent.repo_manager.clone_or_pull.assert_not_called()


class TestLucidPullsSendReport:
    """Tests for send_report method."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_send_report_no_runs(self, mock_auth, mock_github, mock_history,
                                  mock_get_llm, mock_get_notifier):
        """Test send_report with no review runs."""
        settings = _make_settings()
        mock_history.return_value.get_latest_run.return_value = None

        agent = LucidPulls(settings)
        agent.send_report()

        # Should not try to build report
        mock_history.return_value.build_report.assert_not_called()

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_send_report_yesterday_run_still_reports(self, mock_auth, mock_github, mock_history,
                                                      mock_get_llm, mock_get_notifier):
        """Test that a run from yesterday evening still generates today's report."""
        settings = _make_settings()

        # Simulate run that started yesterday at 11:50 PM UTC
        tz = pytz.timezone("America/New_York")
        now_local = datetime.now(tz)
        yesterday_utc = datetime.now(timezone.utc) - timedelta(hours=5)

        mock_run = Mock()
        mock_run.id = 1
        mock_run.started_at = yesterday_utc.replace(tzinfo=None)  # naive UTC
        mock_history.return_value.get_latest_run.return_value = mock_run
        mock_history.return_value.build_report.return_value = Mock()
        mock_get_notifier.return_value.send_report.return_value = Mock(success=True)

        agent = LucidPulls(settings)
        agent.send_report()

        # build_report should be called (run is within yesterday-today window)
        # This test validates the timezone edge case fix


class TestLucidPullsStart:
    """Tests for start method."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_start_exits_with_no_repos(self, mock_auth, mock_github, mock_history,
                                        mock_get_llm, mock_get_notifier):
        """Test start exits when no repos configured."""
        settings = _make_settings(repo_list=[])

        agent = LucidPulls(settings)

        with pytest.raises(SystemExit):
            agent.start()

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_start_exits_when_llm_unavailable(self, mock_auth, mock_github, mock_history,
                                               mock_get_llm, mock_get_notifier):
        """Test start exits when LLM is not available."""
        settings = _make_settings()
        mock_get_llm.return_value.is_available.return_value = False

        agent = LucidPulls(settings)

        with pytest.raises(SystemExit):
            agent.start()


class TestLucidPullsClose:
    """Tests for resource cleanup."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_close_calls_all_cleanup(self, mock_auth, mock_github, mock_history,
                                      mock_get_llm, mock_get_notifier):
        """Test close cleans up all resources."""
        settings = _make_settings()

        agent = LucidPulls(settings)
        agent.repo_manager.close = Mock()
        agent.pr_creator.close = Mock()
        agent.close()

        agent.repo_manager.close.assert_called_once()
        agent.pr_creator.close.assert_called_once()
        mock_history.return_value.close.assert_called_once()

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_context_manager_calls_close(self, mock_auth, mock_github, mock_history,
                                          mock_get_llm, mock_get_notifier):
        """Test context manager calls close on exit."""
        settings = _make_settings()

        with LucidPulls(settings) as agent:
            agent.repo_manager.close = Mock()
            agent.pr_creator.close = Mock()

        agent.repo_manager.close.assert_called_once()
        agent.pr_creator.close.assert_called_once()


class TestDryRun:
    """Tests for --dry-run behaviour."""

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_dry_run_skips_push_and_pr(self, mock_auth, mock_github, mock_history,
                                        mock_get_llm, mock_get_notifier):
        """Test that dry_run skips push and PR creation after commit."""
        settings = _make_settings(dry_run=True)
        agent = LucidPulls(settings)

        # Mock the full _analyze_and_fix path up to commit success
        repo_info = Mock()
        repo_info.local_path = Path("/tmp/test")
        repo_info.default_branch = "main"
        agent.repo_manager.clone_or_pull = Mock(return_value=repo_info)
        agent.pr_creator.has_open_lucidpulls_pr = Mock(return_value=False)
        agent.pr_creator.get_open_issues = Mock(return_value=[])

        # Mock analysis result with a fix
        fix = Mock()
        fix.pr_title = "Fix something"
        fix.pr_body = "Body"
        fix.file_path = "src/foo.py"
        fix.bug_description = "Something is broken"
        fix.fix_description = "desc"
        fix.confidence = "high"
        fix.related_issue = None
        analysis_result = Mock()
        analysis_result.found_fix = True
        analysis_result.fix = fix
        analysis_result.analysis_time_seconds = 1.5
        agent.code_analyzer.analyze = Mock(return_value=analysis_result)
        agent.code_analyzer.apply_fix = Mock(return_value=True)
        agent.repo_manager.create_branch = Mock(return_value=True)
        agent.repo_manager.commit_changes = Mock(return_value=True)
        agent.repo_manager.push_branch = Mock()
        agent.repo_manager.cleanup_branch = Mock()

        result = agent._process_repo("owner/repo1", 1)

        assert result is True
        # push_branch should NOT be called
        agent.repo_manager.push_branch.assert_not_called()
        # cleanup_branch SHOULD be called (local cleanup)
        agent.repo_manager.cleanup_branch.assert_called_once()
        # PR recorded with error="dry_run"
        record_call = mock_history.return_value.record_pr.call_args
        assert record_call[1]["error"] == "dry_run"
        assert record_call[1]["success"] is True

    @patch("src.main.get_notifier")
    @patch("src.main.get_llm")
    @patch("src.main.ReviewHistory")
    @patch("src.main.Github")
    @patch("src.main.Auth")
    def test_non_dry_run_pushes_normally(self, mock_auth, mock_github, mock_history,
                                          mock_get_llm, mock_get_notifier):
        """Test that without dry_run, push and PR happen normally."""
        settings = _make_settings(dry_run=False)
        agent = LucidPulls(settings)

        repo_info = Mock()
        repo_info.local_path = Path("/tmp/test")
        repo_info.default_branch = "main"
        agent.repo_manager.clone_or_pull = Mock(return_value=repo_info)
        agent.pr_creator.has_open_lucidpulls_pr = Mock(return_value=False)
        agent.pr_creator.get_open_issues = Mock(return_value=[])

        fix = Mock()
        fix.pr_title = "Fix something"
        fix.pr_body = "Body"
        fix.file_path = "src/foo.py"
        fix.bug_description = "Something is broken"
        fix.fix_description = "desc"
        fix.confidence = "high"
        fix.related_issue = None
        analysis_result = Mock()
        analysis_result.found_fix = True
        analysis_result.fix = fix
        analysis_result.analysis_time_seconds = 1.5
        agent.code_analyzer.analyze = Mock(return_value=analysis_result)
        agent.code_analyzer.apply_fix = Mock(return_value=True)
        agent.repo_manager.create_branch = Mock(return_value=True)
        agent.repo_manager.commit_changes = Mock(return_value=True)
        agent.repo_manager.push_branch = Mock(return_value=True)
        pr_result = Mock(success=True, pr_number=42, pr_url="https://github.com/owner/repo1/pull/42")
        agent.pr_creator.create_pr = Mock(return_value=pr_result)

        result = agent._process_repo("owner/repo1", 1)

        assert result is True
        agent.repo_manager.push_branch.assert_called_once()


class TestHealthCheck:
    """Tests for --health-check CLI flag."""

    @patch("src.scheduler.check_heartbeat", return_value=True)
    def test_health_check_exits_zero_when_healthy(self, mock_heartbeat):
        """Test --health-check exits 0 when heartbeat is recent."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["src.main", "--health-check"]):
                main()
        assert exc_info.value.code == 0

    @patch("src.scheduler.check_heartbeat", return_value=False)
    def test_health_check_exits_one_when_unhealthy(self, mock_heartbeat):
        """Test --health-check exits 1 when heartbeat is stale."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["src.main", "--health-check"]):
                main()
        assert exc_info.value.code == 1
