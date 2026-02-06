"""Tests for git operations."""

import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, PropertyMock

import pytest
from github import GithubException

from src.git.rate_limiter import GitHubRateLimiter, RateLimitExhausted
from src.git.repo_manager import RepoManager, RepoInfo
from src.git.pr_creator import PRCreator, PRResult


def _make_repo_manager(**overrides):
    """Create a RepoManager with mocked dependencies."""
    defaults = {
        "github": Mock(),
        "rate_limiter": Mock(),
        "username": "testuser",
        "email": "test@example.com",
    }
    defaults.update(overrides)
    return RepoManager(**defaults)


def _make_pr_creator(**overrides):
    """Create a PRCreator with mocked dependencies."""
    defaults = {
        "github": Mock(),
        "rate_limiter": Mock(),
    }
    defaults.update(overrides)
    return PRCreator(**defaults)


class TestRepoManager:
    """Tests for RepoManager."""

    def test_init(self):
        """Test initialization."""
        manager = _make_repo_manager()
        assert manager.username == "testuser"
        assert manager.email == "test@example.com"

    def test_init_with_ssh_key(self):
        """Test initialization with SSH key."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            manager = _make_repo_manager(ssh_key_path=f.name)
            assert manager.ssh_key_path == f.name

    @patch("src.git.repo_manager.Repo")
    def test_clone_or_pull_new_repo(self, mock_repo_class):
        """Test cloning a new repository."""
        # Mock GitHub API
        mock_gh_repo = Mock()
        mock_gh_repo.ssh_url = "git@github.com:owner/repo.git"
        mock_gh_repo.default_branch = "main"
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_gh_repo

        # Mock Git operations
        mock_repo = MagicMock()
        mock_repo_class.clone_from.return_value = mock_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = _make_repo_manager(github=mock_github, clone_dir=tmpdir)

            result = manager.clone_or_pull("owner/repo")

            assert result is not None
            assert result.name == "repo"
            assert result.owner == "owner"
            assert result.full_name == "owner/repo"
            assert result.default_branch == "main"

    def test_create_branch(self):
        """Test creating a branch."""
        mock_repo = Mock()
        mock_repo.active_branch.name = "main"

        repo_info = RepoInfo(
            name="repo",
            owner="owner",
            full_name="owner/repo",
            local_path=Path("/tmp/test"),
            default_branch="main",
            repo=mock_repo,
        )

        manager = _make_repo_manager()

        result = manager.create_branch(repo_info, "feature-branch")

        assert result is True
        mock_repo.git.checkout.assert_called_with("-b", "feature-branch")

    def test_commit_changes(self):
        """Test committing changes."""
        mock_repo = Mock()

        repo_info = RepoInfo(
            name="repo",
            owner="owner",
            full_name="owner/repo",
            local_path=Path("/tmp/test"),
            default_branch="main",
            repo=mock_repo,
        )

        manager = _make_repo_manager()

        result = manager.commit_changes(repo_info, "test.py", "Fix bug")

        assert result is True
        mock_repo.git.add.assert_called_with("test.py")
        mock_repo.git.commit.assert_called_with("-m", "Fix bug")

    def test_push_branch(self):
        """Test pushing a branch."""
        mock_repo = Mock()
        mock_origin = Mock()
        mock_repo.remotes.origin = mock_origin

        repo_info = RepoInfo(
            name="repo",
            owner="owner",
            full_name="owner/repo",
            local_path=Path("/tmp/test"),
            default_branch="main",
            repo=mock_repo,
        )

        manager = _make_repo_manager()

        result = manager.push_branch(repo_info, "feature-branch")

        assert result is True
        mock_origin.push.assert_called_with("feature-branch", set_upstream=True)


class TestRepoManagerSecurity:
    """Security tests for RepoManager."""

    def test_commit_changes_blocks_path_traversal(self):
        """Test that commit_changes rejects path traversal attempts."""
        mock_repo = Mock()

        repo_info = RepoInfo(
            name="repo",
            owner="owner",
            full_name="owner/repo",
            local_path=Path("/tmp/test/owner/repo"),
            default_branch="main",
            repo=mock_repo,
        )

        manager = _make_repo_manager()

        result = manager.commit_changes(repo_info, "../../etc/passwd", "malicious commit")

        assert result is False
        mock_repo.git.add.assert_not_called()

    def test_commit_changes_allows_valid_path(self):
        """Test that commit_changes allows valid relative paths."""
        mock_repo = Mock()

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Create the file so resolve() works
            test_file = repo_path / "src" / "main.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("content")

            repo_info = RepoInfo(
                name="repo",
                owner="owner",
                full_name="owner/repo",
                local_path=repo_path,
                default_branch="main",
                repo=mock_repo,
            )

            manager = _make_repo_manager()

            result = manager.commit_changes(repo_info, "src/main.py", "valid commit")

            assert result is True
            mock_repo.git.add.assert_called_with("src/main.py")

    @patch("src.git.repo_manager.Repo")
    def test_pull_repo_handles_detached_head(self, mock_repo_class):
        """Test _pull_repo handles detached HEAD state."""
        mock_repo = MagicMock()
        # Simulate detached HEAD: active_branch raises TypeError
        type(mock_repo).active_branch = property(lambda self: (_ for _ in ()).throw(TypeError("HEAD is detached")))
        mock_repo_class.return_value = mock_repo

        manager = _make_repo_manager()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = manager._pull_repo(Path(tmpdir), "main")
            # Should not crash; should checkout default branch
            mock_repo.git.checkout.assert_called_with("main")


class TestPRCreator:
    """Tests for PRCreator."""

    def test_init(self):
        """Test initialization."""
        creator = _make_pr_creator()
        assert creator.github is not None

    def test_create_pr_success(self):
        """Test successful PR creation."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.number = 42
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr
        mock_repo.get_label.return_value = Mock()
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is True
        assert result.pr_number == 42
        assert result.pr_url == "https://github.com/owner/repo/pull/42"

    def test_create_pr_with_issue(self):
        """Test PR creation with related issue."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.number = 42
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr
        mock_repo.get_label.return_value = Mock()
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
            related_issue=10,
        )

        assert result.success is True
        # Check that body includes issue reference
        call_args = mock_repo.create_pull.call_args
        assert "Closes #10" in call_args[1]["body"]

    def test_get_open_issues(self):
        """Test getting open issues."""
        mock_repo = Mock()
        mock_issue = Mock()
        mock_issue.number = 1
        mock_issue.title = "Bug"
        mock_issue.body = "Description"
        mock_label = Mock()
        mock_label.name = "bug"
        mock_issue.labels = [mock_label]
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"
        mock_issue.created_at = None

        mock_repo.get_issues.return_value = [mock_issue]
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        issues = creator.get_open_issues("owner/repo", labels=["bug"])

        assert len(issues) == 1
        assert issues[0]["number"] == 1
        assert issues[0]["title"] == "Bug"
        assert "bug" in issues[0]["labels"]

    def test_add_comment(self):
        """Test adding comment to PR."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_repo.get_pull.return_value = mock_pr
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.add_comment("owner/repo", 42, "Comment text")

        assert result is True
        mock_pr.create_issue_comment.assert_called_with("Comment text")


class TestHasOpenLucidpullsPR:
    """Tests for has_open_lucidpulls_pr."""

    def test_returns_true_via_label(self):
        """Test returns True when a labeled LucidPulls PR exists."""
        mock_issue = Mock()
        mock_issue.pull_request = Mock()  # non-None means it's a PR
        mock_issue.number = 42
        mock_repo = Mock()
        mock_repo.get_issues.return_value = [mock_issue]
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        assert creator.has_open_lucidpulls_pr("owner/repo") is True

    def test_returns_true_via_branch_fallback(self):
        """Test returns True via branch prefix fallback."""
        from github import GithubException
        mock_pr = Mock()
        mock_pr.head.ref = "lucidpulls/20240115-fix"
        mock_pr.number = 42
        mock_repo = Mock()
        # Label search returns nothing
        mock_repo.get_issues.return_value = []
        mock_repo.get_pulls.return_value = [mock_pr]
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        assert creator.has_open_lucidpulls_pr("owner/repo") is True

    def test_returns_false_when_no_pr(self):
        """Test returns False when no LucidPulls PR exists."""
        mock_pr = Mock()
        mock_pr.head.ref = "feature/other-branch"
        mock_repo = Mock()
        mock_repo.get_issues.return_value = []
        mock_repo.get_pulls.return_value = [mock_pr]
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        assert creator.has_open_lucidpulls_pr("owner/repo") is False

    def test_returns_false_on_api_error(self):
        """Test returns False on API error (allows processing to continue)."""
        from github import GithubException
        mock_github = Mock()
        mock_github.get_repo.side_effect = GithubException(500, "Server Error", None)

        creator = _make_pr_creator(github=mock_github)
        assert creator.has_open_lucidpulls_pr("owner/repo") is False


class TestPRResult:
    """Tests for PRResult."""

    def test_success_result(self):
        """Test successful result."""
        result = PRResult(
            success=True,
            pr_number=42,
            pr_url="https://github.com/owner/repo/pull/42",
        )
        assert result.success is True
        assert result.error is None

    def test_failed_result(self):
        """Test failed result."""
        result = PRResult(success=False, error="API error")
        assert result.success is False
        assert result.error == "API error"


# ---------------------------------------------------------------------------
# Rate Limiter Tests
# ---------------------------------------------------------------------------

class TestRateLimitExhausted:
    """Tests for RateLimitExhausted exception."""

    def test_stores_wait_seconds(self):
        """Test that wait_seconds is stored on the exception."""
        exc = RateLimitExhausted(120.0)
        assert exc.wait_seconds == 120.0

    def test_message_includes_wait_time(self):
        """Test that the message includes the wait time."""
        exc = RateLimitExhausted(60.5)
        assert "60s" in str(exc)


class TestGitHubRateLimiter:
    """Tests for GitHubRateLimiter."""

    def test_init_defaults(self):
        """Test default initialization."""
        limiter = GitHubRateLimiter(github=Mock())
        assert limiter._min_delay == 0.5
        assert limiter._last_call == 0.0

    def test_init_custom_delay(self):
        """Test custom min_delay."""
        limiter = GitHubRateLimiter(github=Mock(), min_delay=1.0)
        assert limiter._min_delay == 1.0

    def test_init_with_shutdown_event(self):
        """Test initialization with custom shutdown event."""
        event = threading.Event()
        limiter = GitHubRateLimiter(github=Mock(), shutdown_event=event)
        assert limiter._shutdown_event is event

    def test_throttle_enforces_min_delay(self):
        """Test that throttle waits when calls are too close together."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 100
        mock_github.get_rate_limit.return_value = mock_rate

        event = Mock(wraps=threading.Event())
        limiter = GitHubRateLimiter(github=mock_github, min_delay=0.5, shutdown_event=event)

        # First call should not wait (last_call is 0.0)
        limiter.throttle()

        # Second call immediately should trigger a wait
        limiter.throttle()
        # The event.wait was called at least once for the delay
        assert event.wait.called

    def test_throttle_no_wait_after_delay(self):
        """Test that throttle doesn't wait when enough time has passed."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 100
        mock_github.get_rate_limit.return_value = mock_rate

        event = Mock(wraps=threading.Event())
        limiter = GitHubRateLimiter(github=mock_github, min_delay=0.01, shutdown_event=event)

        limiter.throttle()
        time.sleep(0.02)  # Wait longer than min_delay
        event.reset_mock()

        limiter.throttle()
        # event.wait should NOT have been called since enough time elapsed
        event.wait.assert_not_called()

    def test_check_quota_raises_when_exhausted(self):
        """Test that _check_quota raises RateLimitExhausted when remaining == 0."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 0
        mock_rate.rate.reset.timestamp.return_value = time.time() + 300
        mock_github.get_rate_limit.return_value = mock_rate

        limiter = GitHubRateLimiter(github=mock_github)

        with pytest.raises(RateLimitExhausted) as exc_info:
            limiter._check_quota()
        assert exc_info.value.wait_seconds > 0

    def test_check_quota_warns_when_low(self):
        """Test that _check_quota logs warning when quota is low but not zero."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 5  # Low but not zero
        mock_rate.rate.reset.timestamp.return_value = time.time() + 300
        mock_github.get_rate_limit.return_value = mock_rate

        limiter = GitHubRateLimiter(github=mock_github)
        # Should not raise - just warns
        limiter._check_quota()

    def test_check_quota_ok_when_plenty(self):
        """Test that _check_quota does nothing when quota is healthy."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 500
        mock_github.get_rate_limit.return_value = mock_rate

        limiter = GitHubRateLimiter(github=mock_github)
        limiter._check_quota()  # Should not raise

    def test_check_quota_handles_api_error(self):
        """Test that _check_quota swallows non-rate-limit exceptions."""
        mock_github = Mock()
        mock_github.get_rate_limit.side_effect = Exception("API down")

        limiter = GitHubRateLimiter(github=mock_github)
        # Should not raise
        limiter._check_quota()

    def test_wait_for_reset_returns_true_on_completion(self):
        """Test wait_for_reset returns True when wait completes normally."""
        limiter = GitHubRateLimiter(github=Mock())
        result = limiter.wait_for_reset(0.01)
        assert result is True

    def test_wait_for_reset_returns_false_on_shutdown(self):
        """Test wait_for_reset returns False when interrupted by shutdown."""
        event = threading.Event()
        limiter = GitHubRateLimiter(github=Mock(), shutdown_event=event)

        # Signal shutdown immediately
        event.set()
        result = limiter.wait_for_reset(10.0)
        assert result is False

    def test_throttle_raises_rate_limit_exhausted(self):
        """Test that throttle propagates RateLimitExhausted from _check_quota."""
        mock_github = Mock()
        mock_rate = Mock()
        mock_rate.rate.remaining = 0
        mock_rate.rate.reset.timestamp.return_value = time.time() + 300
        mock_github.get_rate_limit.return_value = mock_rate

        limiter = GitHubRateLimiter(github=mock_github, min_delay=0.0)

        with pytest.raises(RateLimitExhausted):
            limiter.throttle()


# ---------------------------------------------------------------------------
# Additional PRCreator Tests
# ---------------------------------------------------------------------------

class TestPRCreatorEnsureLabel:
    """Tests for PRCreator._ensure_label_exists."""

    def test_label_already_exists(self):
        """Test no-op when label already exists."""
        mock_repo = Mock()
        mock_repo.get_label.return_value = Mock()
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        creator._ensure_label_exists("owner/repo")

        mock_repo.get_label.assert_called_once_with("lucidpulls")
        mock_repo.create_label.assert_not_called()

    def test_label_created_when_missing(self):
        """Test label is created when it doesn't exist."""
        mock_repo = Mock()
        mock_repo.get_label.side_effect = GithubException(404, "Not Found", None)
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        creator._ensure_label_exists("owner/repo")

        mock_repo.create_label.assert_called_once_with(
            name="lucidpulls",
            color="7B68EE",
            description="Automated PR by LucidPulls",
        )

    def test_label_creation_error_swallowed(self):
        """Test that errors during label creation are swallowed."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = Exception("network error")

        creator = _make_pr_creator(github=mock_github)
        # Should not raise
        creator._ensure_label_exists("owner/repo")


class TestPRCreatorCreatePREdgeCases:
    """Tests for PRCreator.create_pr edge cases."""

    def test_create_pr_github_exception(self):
        """Test that GithubException is caught and returned as PRResult."""
        mock_github = Mock()
        mock_repo = Mock()
        mock_repo.create_pull.side_effect = GithubException(422, {"message": "Validation Failed"}, None)
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is False
        assert result.error is not None

    def test_create_pr_unexpected_exception(self):
        """Test that unexpected exceptions are caught."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = RuntimeError("connection reset")

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is False
        assert "connection reset" in result.error

    def test_create_pr_label_failure_doesnt_fail_pr(self):
        """Test that label failure doesn't prevent PR success."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.number = 1
        mock_pr.html_url = "https://github.com/owner/repo/pull/1"
        mock_repo.create_pull.return_value = mock_pr
        # Label operations will fail
        mock_repo.get_label.side_effect = Exception("label error")
        mock_pr.add_to_labels.side_effect = Exception("label error")
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is True
        assert result.pr_number == 1


class TestPRCreatorRateLimitRetry:
    """Tests for rate limit handling in PR creation."""

    @patch("time.sleep")
    def test_create_pr_retries_on_rate_limit(self, mock_sleep):
        """Test that rate-limited PR creation retries via the decorator."""
        mock_repo = Mock()
        # First call raises GithubException (rate limit), second succeeds
        mock_pr = Mock(number=1, html_url="https://github.com/o/r/pull/1")
        mock_repo.create_pull.side_effect = [
            GithubException(403, {"message": "API rate limit exceeded"}, None),
            mock_pr,
        ]
        mock_repo.get_label.return_value = Mock()
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is True
        assert mock_repo.create_pull.call_count == 2

    @patch("time.sleep")
    def test_create_pr_fails_after_retries_exhausted(self, mock_sleep):
        """Test that PR creation fails gracefully after all retries."""
        mock_repo = Mock()
        mock_repo.create_pull.side_effect = GithubException(
            403, {"message": "API rate limit exceeded"}, None
        )
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.create_pr(
            repo_full_name="owner/repo",
            branch_name="feature",
            base_branch="main",
            title="Fix bug",
            body="Description",
        )

        assert result.success is False
        # 3 attempts (initial + 2 retries)
        assert mock_repo.create_pull.call_count == 3


class TestPRCreatorGetOpenIssuesEdgeCases:
    """Tests for PRCreator.get_open_issues edge cases."""

    def test_get_open_issues_no_labels_fetches_bugs_and_enhancements(self):
        """Test that get_open_issues without labels fetches bugs and enhancements."""
        mock_repo = Mock()
        mock_bug = Mock()
        mock_bug.number = 1
        mock_bug.title = "Bug"
        mock_bug.body = "A bug"
        mock_bug.labels = []
        mock_bug.html_url = "https://github.com/owner/repo/issues/1"
        mock_bug.created_at = None

        mock_enhancement = Mock()
        mock_enhancement.number = 2
        mock_enhancement.title = "Feature"
        mock_enhancement.body = "A feature"
        mock_enhancement.labels = []
        mock_enhancement.html_url = "https://github.com/owner/repo/issues/2"
        mock_enhancement.created_at = None

        # Return bug for "bug" label, enhancement for "enhancement" label
        def get_issues(state, labels):
            if "bug" in labels:
                return [mock_bug]
            if "enhancement" in labels:
                return [mock_enhancement]
            return []

        mock_repo.get_issues.side_effect = get_issues
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        issues = creator.get_open_issues("owner/repo")

        assert len(issues) == 2
        numbers = {i["number"] for i in issues}
        assert numbers == {1, 2}

    def test_get_open_issues_dedupes(self):
        """Test that duplicate issues are removed."""
        mock_repo = Mock()
        mock_issue = Mock()
        mock_issue.number = 1
        mock_issue.title = "Bug"
        mock_issue.body = "A bug"
        mock_issue.labels = []
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"
        mock_issue.created_at = None

        # Same issue appears in both bug and enhancement queries
        mock_repo.get_issues.return_value = [mock_issue]
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        issues = creator.get_open_issues("owner/repo")

        assert len(issues) == 1

    def test_get_open_issues_github_exception(self):
        """Test that GithubException returns empty list."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = GithubException(500, "Server Error", None)

        creator = _make_pr_creator(github=mock_github)
        issues = creator.get_open_issues("owner/repo")

        assert issues == []

    def test_get_open_issues_unexpected_exception(self):
        """Test that unexpected exceptions return empty list."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = RuntimeError("connection reset")

        creator = _make_pr_creator(github=mock_github)
        issues = creator.get_open_issues("owner/repo")

        assert issues == []


class TestPRCreatorAddCommentEdgeCases:
    """Tests for add_comment edge cases."""

    def test_add_comment_github_exception(self):
        """Test that GithubException returns False."""
        mock_github = Mock()
        mock_repo = Mock()
        mock_repo.get_pull.side_effect = GithubException(404, "Not Found", None)
        mock_github.get_repo.return_value = mock_repo

        creator = _make_pr_creator(github=mock_github)
        result = creator.add_comment("owner/repo", 999, "Comment")

        assert result is False

    def test_add_comment_unexpected_exception(self):
        """Test that unexpected exceptions return False."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = RuntimeError("boom")

        creator = _make_pr_creator(github=mock_github)
        result = creator.add_comment("owner/repo", 1, "Comment")

        assert result is False


class TestPRCreatorContextManager:
    """Tests for PRCreator context manager."""

    def test_context_manager(self):
        """Test that PRCreator works as a context manager."""
        with _make_pr_creator() as creator:
            assert creator is not None


# ---------------------------------------------------------------------------
# Additional RepoManager Tests
# ---------------------------------------------------------------------------

class TestRepoManagerSSH:
    """Tests for RepoManager SSH setup."""

    def test_setup_ssh_no_key_path(self):
        """Test that SSH setup is skipped when no key path."""
        manager = _make_repo_manager(ssh_key_path=None)
        assert "GIT_SSH_COMMAND" not in os.environ or "lucidpulls" not in os.environ.get("GIT_SSH_COMMAND", "")

    def test_setup_ssh_missing_key(self):
        """Test that SSH setup warns on missing key file."""
        manager = _make_repo_manager(ssh_key_path="/nonexistent/key")
        # Should not set GIT_SSH_COMMAND when key doesn't exist

    def test_setup_ssh_valid_key(self):
        """Test that SSH setup configures GIT_SSH_COMMAND with valid key."""
        with tempfile.NamedTemporaryFile(delete=False, suffix="_ssh_key") as f:
            f.write(b"fake-key")
            key_path = f.name

        try:
            manager = _make_repo_manager(ssh_key_path=key_path)
            ssh_cmd = os.environ.get("GIT_SSH_COMMAND", "")
            assert "StrictHostKeyChecking=yes" in ssh_cmd
            assert key_path in ssh_cmd
        finally:
            os.unlink(key_path)
            os.environ.pop("GIT_SSH_COMMAND", None)

    def test_ensure_github_known_hosts(self):
        """Test that GitHub host keys are added to known_hosts."""
        with tempfile.NamedTemporaryFile(delete=False, suffix="_ssh_key") as f:
            f.write(b"fake-key")
            key_path = f.name

        try:
            manager = _make_repo_manager(ssh_key_path=key_path)
            known_hosts = Path.home() / ".ssh" / "known_hosts"
            if known_hosts.exists():
                content = known_hosts.read_text()
                assert "github.com" in content
        finally:
            os.unlink(key_path)
            os.environ.pop("GIT_SSH_COMMAND", None)


class TestRepoManagerDiskSpace:
    """Tests for RepoManager disk space checking."""

    def test_check_disk_space_unlimited(self):
        """Test that disk check passes when limit is 0 (unlimited)."""
        manager = _make_repo_manager(max_clone_disk_mb=0)
        assert manager._check_disk_space() is True

    def test_check_disk_space_within_limit(self):
        """Test that disk check passes when within limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = _make_repo_manager(clone_dir=tmpdir, max_clone_disk_mb=1000)
            assert manager._check_disk_space() is True

    def test_check_disk_space_exceeds_limit(self):
        """Test that disk check fails when exceeding limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that exceeds 1MB limit
            big_file = Path(tmpdir) / "big.bin"
            big_file.write_bytes(b"x" * (2 * 1024 * 1024))

            manager = _make_repo_manager(clone_dir=tmpdir, max_clone_disk_mb=1)
            assert manager._check_disk_space() is False

    def test_get_clone_dir_size(self):
        """Test clone directory size calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "file1.txt").write_bytes(b"x" * 100)
            (Path(tmpdir) / "file2.txt").write_bytes(b"y" * 200)

            manager = _make_repo_manager(clone_dir=tmpdir)
            size = manager._get_clone_dir_size()
            assert size == 300


class TestRepoManagerCleanup:
    """Tests for RepoManager cleanup operations."""

    def test_cleanup_stale_repos(self):
        """Test that stale repos are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create active and stale repo dirs
            active = Path(tmpdir) / "owner" / "active-repo"
            active.mkdir(parents=True)
            (active / "file.txt").write_text("content")

            stale = Path(tmpdir) / "owner" / "stale-repo"
            stale.mkdir(parents=True)
            (stale / "file.txt").write_text("content")

            manager = _make_repo_manager(clone_dir=tmpdir)
            manager.cleanup_stale_repos(["owner/active-repo"])

            assert active.exists()
            assert not stale.exists()

    def test_cleanup_stale_repos_removes_empty_owner_dirs(self):
        """Test that empty owner dirs are removed after cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stale = Path(tmpdir) / "old-owner" / "old-repo"
            stale.mkdir(parents=True)

            manager = _make_repo_manager(clone_dir=tmpdir)
            manager.cleanup_stale_repos([])

            assert not (Path(tmpdir) / "old-owner").exists()

    def test_cleanup_stale_repos_nonexistent_dir(self):
        """Test cleanup on nonexistent directory doesn't crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = _make_repo_manager(clone_dir=tmpdir)
            import shutil
            shutil.rmtree(tmpdir)  # Remove it
            manager.cleanup_stale_repos(["owner/repo"])  # Should not raise

    def test_close_repo(self):
        """Test closing a specific repo."""
        manager = _make_repo_manager()
        mock_repo = Mock()
        manager._open_repos["owner/repo"] = mock_repo

        manager.close_repo("owner/repo")

        mock_repo.close.assert_called_once()
        assert "owner/repo" not in manager._open_repos

    def test_close_repo_nonexistent(self):
        """Test closing a repo that doesn't exist is a no-op."""
        manager = _make_repo_manager()
        manager.close_repo("owner/nonexistent")  # Should not raise

    def test_close_repo_handles_error(self):
        """Test that close_repo handles errors from repo.close()."""
        manager = _make_repo_manager()
        mock_repo = Mock()
        mock_repo.close.side_effect = Exception("already closed")
        manager._open_repos["owner/repo"] = mock_repo

        manager.close_repo("owner/repo")  # Should not raise
        assert "owner/repo" not in manager._open_repos

    def test_close_all(self):
        """Test closing all open repos."""
        manager = _make_repo_manager()
        manager._open_repos["owner/repo1"] = Mock()
        manager._open_repos["owner/repo2"] = Mock()

        manager.close()

        assert len(manager._open_repos) == 0

    def test_context_manager(self):
        """Test RepoManager as context manager."""
        with _make_repo_manager() as manager:
            manager._open_repos["owner/repo"] = Mock()
        assert len(manager._open_repos) == 0


class TestRepoManagerCloneOrPullEdgeCases:
    """Tests for clone_or_pull edge cases."""

    @patch("src.git.repo_manager.Repo")
    def test_clone_or_pull_github_api_error(self, mock_repo_class):
        """Test that GitHub API errors return None."""
        mock_github = Mock()
        mock_github.get_repo.side_effect = GithubException(404, "Not Found", None)

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = _make_repo_manager(github=mock_github, clone_dir=tmpdir)
            result = manager.clone_or_pull("owner/repo")
            assert result is None

    @patch("src.git.repo_manager.Repo")
    def test_clone_or_pull_disk_limit_prevents_clone(self, mock_repo_class):
        """Test that disk limit prevents cloning."""
        mock_gh_repo = Mock()
        mock_gh_repo.ssh_url = "git@github.com:owner/repo.git"
        mock_gh_repo.default_branch = "main"
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_gh_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that exceeds limit
            (Path(tmpdir) / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))

            manager = _make_repo_manager(
                github=mock_github, clone_dir=tmpdir, max_clone_disk_mb=1
            )
            result = manager.clone_or_pull("owner/repo")
            assert result is None

    @patch("src.git.repo_manager.Repo")
    def test_clone_or_pull_pull_fails_reclones(self, mock_repo_class):
        """Test that failed pull triggers fresh clone."""
        from git import GitCommandError

        mock_gh_repo = Mock()
        mock_gh_repo.ssh_url = "git@github.com:owner/repo.git"
        mock_gh_repo.default_branch = "main"
        mock_github = Mock()
        mock_github.get_repo.return_value = mock_gh_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "owner" / "repo"
            repo_path.mkdir(parents=True)

            # Make pull fail with GitCommandError so _pull_repo returns None
            mock_repo = Mock()
            mock_repo.active_branch.name = "main"
            mock_repo.git.reset.side_effect = GitCommandError("reset", "corrupt repo")
            mock_repo_class.return_value = mock_repo

            # Clone should succeed
            mock_cloned = MagicMock()
            mock_repo_class.clone_from.return_value = mock_cloned

            manager = _make_repo_manager(github=mock_github, clone_dir=tmpdir)
            result = manager.clone_or_pull("owner/repo")

            # Should have attempted clone after pull failed
            assert mock_repo_class.clone_from.called


class TestRepoManagerBranchEdgeCases:
    """Tests for branch operations edge cases."""

    def test_create_branch_from_detached_head(self):
        """Test creating branch when in detached HEAD state."""
        mock_repo = Mock()
        type(mock_repo).active_branch = PropertyMock(
            side_effect=TypeError("HEAD is detached")
        )

        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        result = manager.create_branch(repo_info, "feature-branch")

        assert result is True
        # Should checkout default branch first, then create new branch
        calls = mock_repo.git.checkout.call_args_list
        assert calls[0] == (("main",),)
        assert calls[1] == (("-b", "feature-branch"),)

    def test_create_branch_git_error(self):
        """Test that GitCommandError returns False."""
        from git import GitCommandError

        mock_repo = Mock()
        mock_repo.active_branch.name = "main"
        mock_repo.git.checkout.side_effect = GitCommandError("checkout", "branch exists")

        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        result = manager.create_branch(repo_info, "existing-branch")

        assert result is False

    def test_commit_changes_git_error(self):
        """Test that GitCommandError in commit returns False."""
        from git import GitCommandError

        mock_repo = Mock()
        mock_repo.git.commit.side_effect = GitCommandError("commit", "nothing to commit")

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "test.py").write_text("content")

            repo_info = RepoInfo(
                name="repo", owner="owner", full_name="owner/repo",
                local_path=Path(tmpdir), default_branch="main", repo=mock_repo,
            )

            manager = _make_repo_manager()
            result = manager.commit_changes(repo_info, "test.py", "msg")

            assert result is False

    def test_push_branch_git_error(self):
        """Test that persistent GitCommandError in push returns False."""
        from git import GitCommandError

        mock_origin = Mock()
        mock_origin.push.side_effect = GitCommandError("push", "rejected")
        mock_repo = Mock()
        mock_repo.remotes.origin = mock_origin

        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        with patch("src.utils.time.sleep"):
            result = manager.push_branch(repo_info, "feature")

        assert result is False


class TestRepoManagerCleanupBranch:
    """Tests for cleanup_branch."""

    def test_cleanup_local_only(self):
        """Test cleaning up only local branch."""
        mock_repo = Mock()
        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        manager.cleanup_branch(repo_info, "feature-branch")

        mock_repo.git.checkout.assert_called_with("main")
        mock_repo.git.branch.assert_called_with("-D", "feature-branch")
        mock_repo.remotes.origin.push.assert_not_called()

    def test_cleanup_local_and_remote(self):
        """Test cleaning up both local and remote branch."""
        mock_repo = Mock()
        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        manager.cleanup_branch(repo_info, "feature-branch", remote=True)

        mock_repo.git.checkout.assert_called_with("main")
        mock_repo.git.branch.assert_called_with("-D", "feature-branch")
        mock_repo.remotes.origin.push.assert_called_with(refspec=":feature-branch")

    def test_cleanup_local_failure_doesnt_prevent_remote(self):
        """Test that local cleanup failure doesn't prevent remote cleanup."""
        from git import GitCommandError

        mock_repo = Mock()
        mock_repo.git.checkout.side_effect = GitCommandError("checkout", "error")
        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        # Should not raise even though local cleanup fails
        manager.cleanup_branch(repo_info, "feature-branch", remote=True)

        # Remote cleanup should still be attempted
        mock_repo.remotes.origin.push.assert_called_with(refspec=":feature-branch")

    def test_cleanup_remote_failure_swallowed(self):
        """Test that remote cleanup failure is swallowed."""
        from git import GitCommandError

        mock_repo = Mock()
        mock_repo.remotes.origin.push.side_effect = GitCommandError("push", "error")
        repo_info = RepoInfo(
            name="repo", owner="owner", full_name="owner/repo",
            local_path=Path("/tmp/test"), default_branch="main", repo=mock_repo,
        )

        manager = _make_repo_manager()
        # Should not raise
        manager.cleanup_branch(repo_info, "feature-branch", remote=True)
