"""Repository management - clone, pull, branch operations."""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError
from github import Github, GithubException

logger = logging.getLogger("lucidpulls.git.repo_manager")


@dataclass
class RepoInfo:
    """Information about a cloned repository."""

    name: str
    owner: str
    full_name: str
    local_path: Path
    default_branch: str
    repo: Repo


class RepoManager:
    """Manages git repository operations."""

    def __init__(
        self,
        github_token: str,
        username: str,
        email: str,
        ssh_key_path: Optional[str] = None,
        clone_dir: str = "/tmp/lucidpulls/repos",
    ):
        """Initialize repository manager.

        Args:
            github_token: GitHub Personal Access Token.
            username: Git username for commits.
            email: Git email for commits.
            ssh_key_path: Path to SSH private key.
            clone_dir: Directory to clone repositories into.
        """
        self.github = Github(github_token)
        self.username = username
        self.email = email
        self.ssh_key_path = ssh_key_path
        self.clone_dir = Path(clone_dir)
        self.clone_dir.mkdir(parents=True, exist_ok=True)

        # Set up SSH environment if key is provided
        self._setup_ssh_env()

    def _setup_ssh_env(self) -> None:
        """Configure SSH environment for git operations."""
        if self.ssh_key_path and Path(self.ssh_key_path).exists():
            # Create GIT_SSH_COMMAND to use specific key
            # Note: StrictHostKeyChecking=no is used to avoid interactive prompts in automation.
            # For higher security environments, pre-populate known_hosts with GitHub's keys instead.
            os.environ["GIT_SSH_COMMAND"] = (
                f"ssh -i {self.ssh_key_path} -o StrictHostKeyChecking=no"
            )
            logger.debug(f"SSH configured with key: {self.ssh_key_path}")

    def clone_or_pull(self, repo_full_name: str) -> Optional[RepoInfo]:
        """Clone a repository or pull latest changes if already cloned.

        Args:
            repo_full_name: Full repository name (owner/repo).

        Returns:
            RepoInfo if successful, None otherwise.
        """
        try:
            # Get repo info from GitHub API
            gh_repo = self.github.get_repo(repo_full_name)
            owner, name = repo_full_name.split("/")
            local_path = self.clone_dir / owner / name
            default_branch = gh_repo.default_branch

            if local_path.exists():
                logger.info(f"Pulling latest changes for {repo_full_name}")
                repo = self._pull_repo(local_path, default_branch)
                if repo is None:
                    # Pull failed and directory was removed, try fresh clone
                    logger.info(f"Pull failed, attempting fresh clone for {repo_full_name}")
                    repo = self._clone_repo(gh_repo.ssh_url, local_path)
            else:
                logger.info(f"Cloning {repo_full_name}")
                repo = self._clone_repo(gh_repo.ssh_url, local_path)

            if repo is None:
                return None

            # Configure git user for this repo
            self._configure_git_user(repo)

            return RepoInfo(
                name=name,
                owner=owner,
                full_name=repo_full_name,
                local_path=local_path,
                default_branch=default_branch,
                repo=repo,
            )
        except GithubException as e:
            logger.error(f"GitHub API error for {repo_full_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to clone/pull {repo_full_name}: {e}")
            return None

    def _clone_repo(self, ssh_url: str, local_path: Path) -> Optional[Repo]:
        """Clone a repository.

        Args:
            ssh_url: SSH URL for the repository.
            local_path: Local path to clone into.

        Returns:
            Git Repo object if successful.
        """
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(ssh_url, local_path)
            logger.debug(f"Cloned to {local_path}")
            return repo
        except GitCommandError as e:
            logger.error(f"Clone failed: {e}")
            return None

    def _pull_repo(self, local_path: Path, default_branch: str) -> Optional[Repo]:
        """Pull latest changes for a repository.

        Args:
            local_path: Local path to the repository.
            default_branch: Default branch name.

        Returns:
            Git Repo object if successful.
        """
        try:
            repo = Repo(local_path)

            # Ensure we're on the default branch
            if repo.active_branch.name != default_branch:
                repo.git.checkout(default_branch)

            # Reset any local changes
            repo.git.reset("--hard", f"origin/{default_branch}")

            # Pull latest
            origin = repo.remotes.origin
            origin.pull()

            logger.debug(f"Pulled latest for {local_path}")
            return repo
        except GitCommandError as e:
            logger.error(f"Pull failed, attempting fresh clone: {e}")
            # If pull fails, remove and re-clone
            shutil.rmtree(local_path, ignore_errors=True)
            return None

    def _configure_git_user(self, repo: Repo) -> None:
        """Configure git user for a repository.

        Args:
            repo: Git Repo object.
        """
        with repo.config_writer() as config:
            config.set_value("user", "name", self.username)
            config.set_value("user", "email", self.email)

    def create_branch(self, repo_info: RepoInfo, branch_name: str) -> bool:
        """Create and checkout a new branch.

        Args:
            repo_info: Repository information.
            branch_name: Name for the new branch.

        Returns:
            True if successful.
        """
        try:
            repo = repo_info.repo

            # Ensure we're on the default branch first
            if repo.active_branch.name != repo_info.default_branch:
                repo.git.checkout(repo_info.default_branch)

            # Create and checkout new branch
            repo.git.checkout("-b", branch_name)
            logger.info(f"Created branch: {branch_name}")
            return True
        except GitCommandError as e:
            logger.error(f"Failed to create branch {branch_name}: {e}")
            return False

    def commit_changes(
        self, repo_info: RepoInfo, file_path: str, message: str
    ) -> bool:
        """Stage and commit changes to a file.

        Args:
            repo_info: Repository information.
            file_path: Relative path to the file.
            message: Commit message.

        Returns:
            True if successful.
        """
        try:
            repo = repo_info.repo
            repo.git.add(file_path)
            repo.git.commit("-m", message)
            logger.info(f"Committed: {message}")
            return True
        except GitCommandError as e:
            logger.error(f"Failed to commit: {e}")
            return False

    def push_branch(self, repo_info: RepoInfo, branch_name: str) -> bool:
        """Push a branch to the remote.

        Args:
            repo_info: Repository information.
            branch_name: Branch name to push.

        Returns:
            True if successful.
        """
        try:
            repo = repo_info.repo
            origin = repo.remotes.origin
            origin.push(branch_name, set_upstream=True)
            logger.info(f"Pushed branch: {branch_name}")
            return True
        except GitCommandError as e:
            logger.error(f"Failed to push branch {branch_name}: {e}")
            return False

    def cleanup_branch(self, repo_info: RepoInfo, branch_name: str) -> None:
        """Delete a local branch and return to default branch.

        Args:
            repo_info: Repository information.
            branch_name: Branch name to delete.
        """
        try:
            repo = repo_info.repo
            repo.git.checkout(repo_info.default_branch)
            repo.git.branch("-D", branch_name)
            logger.debug(f"Cleaned up branch: {branch_name}")
        except GitCommandError as e:
            logger.warning(f"Failed to cleanup branch {branch_name}: {e}")

    def close(self) -> None:
        """Close GitHub client connection."""
        if hasattr(self, "github"):
            self.github.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
