"""Repository management - clone, pull, branch operations."""

import logging
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError
from github import Github, GithubException

from src.git.rate_limiter import GitHubRateLimiter

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
        github: Github,
        rate_limiter: GitHubRateLimiter,
        username: str,
        email: str,
        ssh_key_path: Optional[str] = None,
        clone_dir: str = "/tmp/lucidpulls/repos",
        max_clone_disk_mb: int = 0,
    ):
        """Initialize repository manager.

        Args:
            github: Shared Github client instance.
            rate_limiter: Shared rate limiter instance.
            username: Git username for commits.
            email: Git email for commits.
            ssh_key_path: Path to SSH private key.
            clone_dir: Directory to clone repositories into.
            max_clone_disk_mb: Maximum disk usage for clones in MB (0 = unlimited).
        """
        self.github = github
        self._rate_limiter = rate_limiter
        self.username = username
        self.email = email
        self.ssh_key_path = ssh_key_path
        self.clone_dir = Path(clone_dir)
        self.clone_dir.mkdir(parents=True, exist_ok=True)
        self._max_clone_disk_bytes = max_clone_disk_mb * 1024 * 1024 if max_clone_disk_mb > 0 else 0

        # Track open Repo objects to close them properly
        self._open_repos: dict[str, Repo] = {}

        # Set up SSH environment if key is provided
        self._setup_ssh_env()

    # GitHub's official SSH host keys (from https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
    GITHUB_HOST_KEYS = [
        "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
        "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=",
        "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHdJ1IfGQlQLVcsQ7Iqd9Lo4UaX5UhGwEPa7b4QIYBfGXqTUy8gMaHRr7J/+1YlQAl3FNeaRjTJZAFsQHNkV+T7+3MHwKTwNGMJaSVQi7LcOcAzJWbT3LTamT5n+gSJjWGBiJ0olIigMwkFgMuWjhYvE23DjGqb/MBk5xGIxlGbzWPYPP/ixIqakB9tv3WtOJZXDpiLLHno+sFr/B88CZbqfOAP1joMpIwJMIEBB4BAdxEixjEqr5S/zFIVGFwPKZd+MVAzXPEgFw5DH0WH5OAe6bW6eKdpMGJQJbFmqRYjCjz12F8RO0q2LHCJPCLbNlQ=",
    ]

    def _setup_ssh_env(self) -> None:
        """Configure SSH environment for git operations."""
        if not self.ssh_key_path:
            return

        key_path = Path(self.ssh_key_path)
        if not key_path.exists():
            logger.warning(f"SSH key not found: {self.ssh_key_path}")
            return

        # Verify the key file is readable
        if not os.access(key_path, os.R_OK):
            logger.error(f"SSH key is not readable: {self.ssh_key_path} — check file permissions (should be 600)")
            return

        # Ensure GitHub's host keys are in known_hosts for strict verification
        self._ensure_github_known_hosts()

        # Use StrictHostKeyChecking=yes with pre-populated known_hosts
        os.environ["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(str(self.ssh_key_path))} -o StrictHostKeyChecking=yes"
        )
        logger.debug(f"SSH configured with key: {self.ssh_key_path}")

    def _ensure_github_known_hosts(self) -> None:
        """Pre-populate known_hosts with GitHub's official SSH host keys."""
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)

        known_hosts_path = ssh_dir / "known_hosts"

        # Read existing known_hosts content
        existing = ""
        if known_hosts_path.exists():
            existing = known_hosts_path.read_text()

        # Add any missing GitHub host keys
        added = False
        for key_line in self.GITHUB_HOST_KEYS:
            if key_line not in existing:
                with open(known_hosts_path, "a") as f:
                    f.write(f"{key_line}\n")
                added = True

        if added:
            # Ensure proper permissions
            known_hosts_path.chmod(0o644)
            logger.debug("Added GitHub SSH host keys to known_hosts")

    def _get_clone_dir_size(self) -> int:
        """Get total size of the clone directory in bytes."""
        total = 0
        try:
            for entry in self.clone_dir.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def _check_disk_space(self) -> bool:
        """Check if clone directory is within disk space limits.

        Returns:
            True if within limits or limits are disabled.
        """
        if self._max_clone_disk_bytes == 0:
            return True
        current_size = self._get_clone_dir_size()
        if current_size > self._max_clone_disk_bytes:
            logger.warning(
                f"Clone directory exceeds disk limit: "
                f"{current_size / 1024 / 1024:.0f}MB / "
                f"{self._max_clone_disk_bytes / 1024 / 1024:.0f}MB"
            )
            return False
        return True

    def cleanup_stale_repos(self, active_repos: list[str]) -> None:
        """Remove cloned repos not in the active repo list."""
        if not self.clone_dir.exists():
            return
        active_set = set()
        for repo_name in active_repos:
            parts = repo_name.split("/")
            if len(parts) == 2:
                active_set.add(self.clone_dir / parts[0] / parts[1])

        for owner_dir in self.clone_dir.iterdir():
            if not owner_dir.is_dir():
                continue
            for repo_dir in owner_dir.iterdir():
                if repo_dir.is_dir() and repo_dir not in active_set:
                    logger.info(f"Cleaning stale repo: {repo_dir}")
                    shutil.rmtree(repo_dir, ignore_errors=True)
            # Remove empty owner dirs
            if not any(owner_dir.iterdir()):
                owner_dir.rmdir()

    def clone_or_pull(self, repo_full_name: str) -> Optional[RepoInfo]:
        """Clone a repository or pull latest changes if already cloned.

        Args:
            repo_full_name: Full repository name (owner/repo).

        Returns:
            RepoInfo if successful, None otherwise.
        """
        self._rate_limiter.throttle()
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
                    if not self._check_disk_space():
                        logger.error(f"Skipping clone of {repo_full_name}: disk space limit exceeded")
                        return None
                    repo = self._clone_repo(gh_repo.ssh_url, local_path)
            else:
                if not self._check_disk_space():
                    logger.error(f"Skipping clone of {repo_full_name}: disk space limit exceeded")
                    return None
                logger.info(f"Cloning {repo_full_name}")
                repo = self._clone_repo(gh_repo.ssh_url, local_path)

            if repo is None:
                return None

            # Configure git user for this repo
            self._configure_git_user(repo)

            # Track the repo for cleanup
            self._open_repos[repo_full_name] = repo

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
        """Clone a repository using shallow clone.

        Args:
            ssh_url: SSH URL for the repository.
            local_path: Local path to clone into.

        Returns:
            Git Repo object if successful.
        """
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            repo = Repo.clone_from(ssh_url, local_path, depth=1)
            logger.debug(f"Shallow cloned to {local_path}")
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

            # Ensure we're on the default branch (handle detached HEAD)
            try:
                current_branch = repo.active_branch.name
            except TypeError:
                # Detached HEAD state
                current_branch = None
            if current_branch != default_branch:
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

            # Ensure we're on the default branch first (handle detached HEAD)
            try:
                current_branch = repo.active_branch.name
            except TypeError:
                current_branch = None
            if current_branch != repo_info.default_branch:
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

            # Security: Validate file_path is within the repo
            resolved = (repo_info.local_path / file_path).resolve()
            if not resolved.is_relative_to(repo_info.local_path.resolve()):
                logger.error(f"Path traversal detected in commit: {file_path}")
                return False

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

    def cleanup_branch(self, repo_info: RepoInfo, branch_name: str, remote: bool = False) -> None:
        """Delete a local branch and return to default branch.

        Args:
            repo_info: Repository information.
            branch_name: Branch name to delete.
            remote: If True, also delete the remote branch.
        """
        try:
            repo = repo_info.repo
            repo.git.checkout(repo_info.default_branch)
            repo.git.branch("-D", branch_name)
            logger.debug(f"Cleaned up local branch: {branch_name}")
        except GitCommandError as e:
            logger.warning(f"Failed to cleanup local branch {branch_name}: {e}")

        if remote:
            try:
                repo_info.repo.remotes.origin.push(refspec=f":{branch_name}")
                logger.debug(f"Cleaned up remote branch: {branch_name}")
            except GitCommandError as e:
                logger.warning(f"Failed to cleanup remote branch {branch_name}: {e}")

    def close_repo(self, repo_full_name: str) -> None:
        """Close a specific repository and release its resources.

        Args:
            repo_full_name: Full repository name (owner/repo).
        """
        if repo_full_name in self._open_repos:
            repo = self._open_repos.pop(repo_full_name)
            try:
                repo.close()
            except Exception as e:
                logger.debug(f"Error closing repo {repo_full_name}: {e}")

    def close(self) -> None:
        """Close all open repository connections."""
        # Close all tracked Repo objects
        for repo_name in list(self._open_repos.keys()):
            self.close_repo(repo_name)

        # Clean up SSH environment
        os.environ.pop("GIT_SSH_COMMAND", None)

    def __enter__(self) -> "RepoManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
