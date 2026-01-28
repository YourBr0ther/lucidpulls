"""Main entry point and orchestration for LucidPulls."""

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

from src import setup_logging
from src.utils import sanitize_branch_name
from src.config import get_settings, Settings
from src.database import ReviewHistory
from src.git import RepoManager, PRCreator
from src.llm import get_llm
from src.analyzers import CodeAnalyzer, IssueAnalyzer
from src.notifications import get_notifier
from src.scheduler import ReviewScheduler, DeadlineEnforcer

logger = logging.getLogger("lucidpulls.main")


class LucidPulls:
    """Main orchestrator for the LucidPulls agent."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize LucidPulls.

        Args:
            settings: Optional settings override.
        """
        self.settings = settings or get_settings()

        # Initialize components
        self.history = ReviewHistory()
        self.repo_manager = RepoManager(
            github_token=self.settings.github_token,
            username=self.settings.github_username,
            email=self.settings.github_email,
            ssh_key_path=self.settings.ssh_key_path,
            clone_dir=self.settings.clone_dir,
        )
        self.pr_creator = PRCreator(github_token=self.settings.github_token)

        # Initialize LLM
        llm_config = self.settings.get_llm_config()
        self.llm = get_llm(self.settings.llm_provider, llm_config)

        # Initialize analyzers
        self.code_analyzer = CodeAnalyzer(self.llm)
        self.issue_analyzer = IssueAnalyzer()

        # Initialize notifier
        notifier_config = self.settings.get_notification_config()
        self.notifier = get_notifier(self.settings.notification_channel, notifier_config)

        # Scheduler and deadline enforcer
        self.scheduler = ReviewScheduler(timezone=self.settings.timezone)
        self.deadline = DeadlineEnforcer(
            deadline_time=self.settings.schedule_deadline,
            timezone=self.settings.timezone,
        )

        # Shutdown flag with lock for thread safety
        self._shutdown = False
        self._lock = threading.Lock()
        # Event is set when idle, cleared while _process_repo is running
        self._idle = threading.Event()
        self._idle.set()

    def run_review(self) -> None:
        """Run the nightly review process."""
        logger.info("Starting nightly review")

        # Mark the deadline anchor for this review cycle
        self.deadline.mark_review_started()

        # Start tracking this run
        run_id = self.history.start_run()

        repos = self.settings.repo_list
        if not repos:
            logger.info("No repositories configured")
            self.history.complete_run(run_id, 0, 0)
            return

        # Clean up stale repo clones
        self.repo_manager.cleanup_stale_repos(repos)

        logger.info(f"Processing {len(repos)} repositories")

        prs_created = 0
        repos_reviewed = 0

        for repo_name in repos:
            with self._lock:
                if self._shutdown:
                    logger.info("Shutdown requested, stopping review")
                    break

            if self.deadline.is_past_deadline():
                logger.warning("Deadline reached, stopping review")
                break

            try:
                self._idle.clear()
                result = self._process_repo(repo_name, run_id)
                repos_reviewed += 1
                if result:
                    prs_created += 1
            except Exception as e:
                logger.error(f"Error processing {repo_name}: {e}")
                try:
                    self.history.record_pr(
                        run_id,
                        repo_name=repo_name,
                        success=False,
                        error=str(e),
                    )
                except Exception as db_err:
                    logger.error(f"Failed to record error for {repo_name}: {db_err}")
            finally:
                self._idle.set()

        # Complete the run
        self.history.complete_run(run_id, repos_reviewed, prs_created)
        logger.info(f"Review complete: {repos_reviewed} repos, {prs_created} PRs")

    def _process_repo(self, repo_name: str, run_id: int) -> bool:
        """Process a single repository.

        Args:
            repo_name: Full repository name (owner/repo).
            run_id: The current review run ID.

        Returns:
            True if a PR was created.
        """
        logger.info(f"Processing {repo_name}")

        # Clone or pull the repository
        repo_info = self.repo_manager.clone_or_pull(repo_name)
        if not repo_info:
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to clone/pull repository",
            )
            return False

        try:
            return self._analyze_and_fix(repo_name, repo_info, run_id)
        finally:
            # Always release repo resources after processing
            self.repo_manager.close_repo(repo_name)

    def _analyze_and_fix(self, repo_name: str, repo_info, run_id: int) -> bool:
        """Run analysis and apply fix for a repo. Separated for resource cleanup.

        Args:
            repo_name: Full repository name (owner/repo).
            repo_info: RepoInfo from clone_or_pull.
            run_id: The current review run ID.

        Returns:
            True if a PR was created.
        """
        # Check for existing LucidPulls PR early (before expensive LLM analysis)
        if self.pr_creator.has_open_lucidpulls_pr(repo_name):
            logger.info(f"Skipping {repo_name}: existing LucidPulls PR found")
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Existing LucidPulls PR already open",
            )
            return False

        # Get open issues (used to guide analysis, but not required)
        issues = self.pr_creator.get_open_issues(repo_name)
        issues = self.issue_analyzer.filter_actionable(issues)
        issues = self.issue_analyzer.prioritize(issues, limit=5)

        if not issues:
            logger.info(f"No actionable issues for {repo_name}, analyzing code only")

        # Analyze code (issues guide the LLM but aren't required)
        result = self.code_analyzer.analyze(
            repo_path=repo_info.local_path,
            repo_name=repo_name,
            issues=issues,
        )

        if not result.found_fix or not result.fix:
            logger.debug(f"No actionable fix found for {repo_name}")
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="No actionable fixes identified",
            )
            return False

        fix = result.fix
        logger.info(f"Found fix: {fix.pr_title}")

        # Create branch with sanitized file path
        safe_file = sanitize_branch_name(fix.file_path)
        tz = pytz.timezone(self.settings.timezone)
        branch_name = f"lucidpulls/{datetime.now(tz).strftime('%Y%m%d-%H%M%S')}-{safe_file}"
        if not self.repo_manager.create_branch(repo_info, branch_name):
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to create branch",
            )
            return False

        # Apply fix
        if not self.code_analyzer.apply_fix(repo_info.local_path, fix):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to apply fix",
            )
            return False

        # Commit changes
        commit_msg = f"{fix.pr_title}\n\n{fix.fix_description}"
        if not self.repo_manager.commit_changes(repo_info, fix.file_path, commit_msg):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to commit changes",
            )
            return False

        # Push branch
        if not self.repo_manager.push_branch(repo_info, branch_name):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to push branch",
            )
            return False

        # Create PR
        pr_result = self.pr_creator.create_pr(
            repo_full_name=repo_name,
            branch_name=branch_name,
            base_branch=repo_info.default_branch,
            title=fix.pr_title,
            body=fix.pr_body,
            related_issue=fix.related_issue,
        )

        if not pr_result.success:
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error=pr_result.error or "Failed to create PR",
            )
            return False

        # Record success
        self.history.record_pr(
            run_id,
            repo_name=repo_name,
            pr_number=pr_result.pr_number,
            pr_url=pr_result.pr_url,
            pr_title=fix.pr_title,
            success=True,
        )

        logger.info(f"Created PR #{pr_result.pr_number}: {pr_result.pr_url}")
        return True

    def test_notifications(self) -> None:
        """Send a test notification to verify delivery is working."""
        from src.notifications.base import PRSummary, ReviewReport

        logger.info("Sending test notification")

        if not self.notifier.is_configured():
            logger.error(
                f"Notification channel {self.notifier.channel_name} is not configured"
            )
            return

        tz = pytz.timezone(self.settings.timezone)
        now = datetime.now(tz)
        report = ReviewReport(
            date=now,
            repos_reviewed=2,
            prs_created=1,
            start_time=now.replace(hour=2, minute=0, second=0),
            end_time=now.replace(hour=3, minute=42, second=0),
            prs=[
                PRSummary(
                    repo_name="example/sample-repo",
                    pr_number=42,
                    pr_url="https://github.com/example/sample-repo/pull/42",
                    pr_title="Fix null check in request handler",
                    success=True,
                ),
                PRSummary(
                    repo_name="example/other-repo",
                    pr_number=None,
                    pr_url=None,
                    pr_title=None,
                    success=False,
                    error="No actionable issues found",
                ),
            ],
        )

        result = self.notifier.send_report(report)
        if result.success:
            logger.info(f"Test notification sent via {self.notifier.channel_name}")
        else:
            logger.error(f"Test notification failed: {result.error}")

    def send_report(self) -> None:
        """Send the morning report notification."""
        logger.info("Preparing morning report")

        # Get the latest completed run
        run = self.history.get_latest_run()
        if not run:
            logger.warning("No review runs found for report")
            return

        # Convert UTC to local timezone for comparison
        tz = pytz.timezone(self.settings.timezone)
        now_local = datetime.now(tz)
        today = now_local.date()
        # run.started_at is naive UTC, make it aware then convert to local
        run_started_local = pytz.utc.localize(run.started_at).astimezone(tz)
        run_date = run_started_local.date()

        # Allow runs that started yesterday evening (before midnight) to match today's report,
        # since a 11:50 PM start that finishes at 12:10 AM should still report in the morning.
        yesterday = today - timedelta(days=1)
        if run_date != today and run_date != yesterday:
            logger.info(f"Latest run was on {run_date}, skipping report for today")
            return

        # Build and send report
        report = self.history.build_report(run.id)
        if not report:
            logger.error("Failed to build report")
            return

        result = self.notifier.send_report(report)
        if result.success:
            logger.info(f"Report sent via {self.notifier.channel_name}")
        else:
            logger.error(f"Failed to send report: {result.error}")

    def start(self) -> None:
        """Start the LucidPulls service."""
        logger.info("Starting LucidPulls service")

        # Validate required configuration
        if not self.settings.repo_list:
            logger.error("No repositories configured - set REPOS environment variable")
            sys.exit(1)

        # Check LLM availability
        if not self.llm.is_available():
            logger.error(f"LLM provider {self.llm.provider_name} is not available")
            sys.exit(1)

        # Validate SSH key if configured
        if self.settings.ssh_key_path:
            key_path = Path(self.settings.ssh_key_path).expanduser()
            if not key_path.exists():
                logger.error(f"SSH key not found: {key_path}")
                sys.exit(1)
            if not os.access(key_path, os.R_OK):
                logger.error(f"SSH key is not readable: {key_path} — check file permissions (should be 600)")
                sys.exit(1)

        # Validate GitHub token by making a lightweight API call
        try:
            from github import Github
            gh = Github(self.settings.github_token)
            gh.get_user().login
            gh.close()
        except Exception as e:
            logger.error(f"GitHub token validation failed: {e}")
            sys.exit(1)

        # Check notifier configuration
        if not self.notifier.is_configured():
            logger.warning(f"Notification channel {self.notifier.channel_name} is not configured")

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Schedule jobs
        self.scheduler.schedule_review(
            start_time=self.settings.schedule_start,
            review_func=self.run_review,
        )
        self.scheduler.schedule_report(
            delivery_time=self.settings.report_delivery,
            report_func=self.send_report,
        )

        # Log next run times
        next_review = self.scheduler.get_next_run_time()
        if next_review:
            logger.info(f"Next review scheduled at: {next_review}")

        # Start scheduler (blocking)
        self.scheduler.start()

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully.

        Sets the shutdown flag and waits for any in-flight repo processing
        to finish before tearing down resources.
        """
        logger.info(f"Received signal {signum}, shutting down...")
        with self._lock:
            self._shutdown = True
        self.scheduler.stop()
        # Wait for any in-progress repo operation to finish (up to 60s)
        if not self._idle.is_set():
            logger.info("Waiting for in-progress repo operation to finish...")
            self._idle.wait(timeout=60)
        self.close()

    def close(self) -> None:
        """Clean up all resources."""
        logger.debug("Closing LucidPulls resources")

        # Close repo manager (includes GitHub client and git repos)
        if hasattr(self, "repo_manager"):
            self.repo_manager.close()

        # Close PR creator (GitHub client)
        if hasattr(self, "pr_creator"):
            self.pr_creator.close()

        # Close LLM client (HTTP client)
        if hasattr(self, "llm") and hasattr(self.llm, "close"):
            self.llm.close()

        # Close notifier (HTTP client)
        if hasattr(self, "notifier") and hasattr(self.notifier, "close"):
            self.notifier.close()

        # Close database engine
        if hasattr(self, "history"):
            self.history.close()

    def __enter__(self) -> "LucidPulls":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="LucidPulls - Code review for bugs while you sleep"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run review immediately instead of waiting for schedule",
    )
    parser.add_argument(
        "--send-report",
        action="store_true",
        help="Send report immediately for latest run",
    )
    parser.add_argument(
        "--test-notifications",
        action="store_true",
        help="Send a test notification to verify delivery",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Set up logging
    settings = get_settings()
    log_level = "DEBUG" if args.debug else settings.log_level
    setup_logging(log_level)

    logger.info("LucidPulls starting up")

    # Create orchestrator and ensure cleanup on exit
    agent = LucidPulls(settings)
    try:
        if args.run_now:
            logger.info("Running review immediately")
            agent.run_review()
        elif args.send_report:
            logger.info("Sending report immediately")
            agent.send_report()
        elif args.test_notifications:
            agent.test_notifications()
        else:
            # Start the scheduled service
            agent.start()
    finally:
        agent.close()


if __name__ == "__main__":
    main()
