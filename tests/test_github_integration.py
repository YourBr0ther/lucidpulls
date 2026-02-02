"""Integration tests against the real GitHub API.

These tests are read-only and skipped unless GITHUB_TOKEN is set.
"""

import os

import pytest
from github import Auth, Github

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER = "YourBr0ther"


@pytest.mark.skipif(not GITHUB_TOKEN, reason="GITHUB_TOKEN not set")
class TestGitHubIntegration:
    """Read-only tests against the live GitHub API."""

    @pytest.fixture(autouse=True)
    def _github_client(self):
        self.gh = Github(auth=Auth.Token(GITHUB_TOKEN), timeout=30)
        yield
        self.gh.close()

    def test_authenticate(self):
        """Verify the token authenticates successfully."""
        user = self.gh.get_user()
        assert user.login == GITHUB_USER

    def test_list_repos(self):
        """Verify we can list repositories for the authenticated user."""
        repos = list(self.gh.get_user().get_repos(type="owner"))
        assert isinstance(repos, list)
        assert len(repos) >= 0  # may be empty for new accounts

    def test_rate_limit_accessible(self):
        """Verify rate limit endpoint is accessible."""
        rate = self.gh.get_rate_limit()
        assert rate.core.limit > 0
        assert rate.core.remaining >= 0
