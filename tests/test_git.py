"""Tests for git operations."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from src.git.repo_manager import RepoManager, RepoInfo
from src.git.pr_creator import PRCreator, PRResult


class TestRepoManager:
    """Tests for RepoManager."""

    def test_init(self):
        """Test initialization."""
        manager = RepoManager(
            github_token="test-token",
            username="testuser",
            email="test@example.com",
        )
        assert manager.username == "testuser"
        assert manager.email == "test@example.com"

    def test_init_with_ssh_key(self):
        """Test initialization with SSH key."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            manager = RepoManager(
                github_token="test-token",
                username="testuser",
                email="test@example.com",
                ssh_key_path=f.name,
            )
            assert manager.ssh_key_path == f.name

    @patch("src.git.repo_manager.Github")
    @patch("src.git.repo_manager.Repo")
    def test_clone_or_pull_new_repo(self, mock_repo_class, mock_github):
        """Test cloning a new repository."""
        # Mock GitHub API
        mock_gh_repo = Mock()
        mock_gh_repo.ssh_url = "git@github.com:owner/repo.git"
        mock_gh_repo.default_branch = "main"
        mock_github.return_value.get_repo.return_value = mock_gh_repo

        # Mock Git operations
        mock_repo = Mock()
        mock_repo_class.clone_from.return_value = mock_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RepoManager(
                github_token="test-token",
                username="testuser",
                email="test@example.com",
                clone_dir=tmpdir,
            )

            result = manager.clone_or_pull("owner/repo")

            assert result is not None
            assert result.name == "repo"
            assert result.owner == "owner"
            assert result.full_name == "owner/repo"
            assert result.default_branch == "main"

    @patch("src.git.repo_manager.Github")
    @patch("src.git.repo_manager.Repo")
    def test_create_branch(self, mock_repo_class, mock_github):
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

        manager = RepoManager(
            github_token="test-token",
            username="testuser",
            email="test@example.com",
        )

        result = manager.create_branch(repo_info, "feature-branch")

        assert result is True
        mock_repo.git.checkout.assert_called_with("-b", "feature-branch")

    @patch("src.git.repo_manager.Github")
    def test_commit_changes(self, mock_github):
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

        manager = RepoManager(
            github_token="test-token",
            username="testuser",
            email="test@example.com",
        )

        result = manager.commit_changes(repo_info, "test.py", "Fix bug")

        assert result is True
        mock_repo.git.add.assert_called_with("test.py")
        mock_repo.git.commit.assert_called_with("-m", "Fix bug")

    @patch("src.git.repo_manager.Github")
    def test_push_branch(self, mock_github):
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

        manager = RepoManager(
            github_token="test-token",
            username="testuser",
            email="test@example.com",
        )

        result = manager.push_branch(repo_info, "feature-branch")

        assert result is True
        mock_origin.push.assert_called_with("feature-branch", set_upstream=True)


class TestPRCreator:
    """Tests for PRCreator."""

    def test_init(self):
        """Test initialization."""
        creator = PRCreator(github_token="test-token")
        assert creator.github is not None

    @patch("src.git.pr_creator.Github")
    def test_create_pr_success(self, mock_github):
        """Test successful PR creation."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.number = 42
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr
        mock_github.return_value.get_repo.return_value = mock_repo

        creator = PRCreator(github_token="test-token")
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

    @patch("src.git.pr_creator.Github")
    def test_create_pr_with_issue(self, mock_github):
        """Test PR creation with related issue."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_pr.number = 42
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr
        mock_github.return_value.get_repo.return_value = mock_repo

        creator = PRCreator(github_token="test-token")
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

    @patch("src.git.pr_creator.Github")
    def test_get_open_issues(self, mock_github):
        """Test getting open issues."""
        mock_repo = Mock()
        mock_issue = Mock()
        mock_issue.number = 1
        mock_issue.title = "Bug"
        mock_issue.body = "Description"
        mock_issue.labels = [Mock(name="bug")]
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"
        mock_issue.created_at = None

        mock_repo.get_issues.return_value = [mock_issue]
        mock_github.return_value.get_repo.return_value = mock_repo

        creator = PRCreator(github_token="test-token")
        issues = creator.get_open_issues("owner/repo", labels=["bug"])

        assert len(issues) == 1
        assert issues[0]["number"] == 1
        assert issues[0]["title"] == "Bug"
        assert "bug" in issues[0]["labels"]

    @patch("src.git.pr_creator.Github")
    def test_add_comment(self, mock_github):
        """Test adding comment to PR."""
        mock_repo = Mock()
        mock_pr = Mock()
        mock_repo.get_pull.return_value = mock_pr
        mock_github.return_value.get_repo.return_value = mock_repo

        creator = PRCreator(github_token="test-token")
        result = creator.add_comment("owner/repo", 42, "Comment text")

        assert result is True
        mock_pr.create_issue_comment.assert_called_with("Comment text")


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
