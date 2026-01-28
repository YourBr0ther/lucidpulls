"""Main entry point and orchestration for LucidPulls."""

import argparse
import logging
import signal
import sys
from datetime import datetime
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

        # Track current run
        self._current_run_id: Optional[int] = None
        self._shutdown = False

    def run_review(self) -> None:
        """Run the nightly review process."""
        logger.info("Starting nightly review")

        # Start tracking this run
        run = self.history.start_run()
        self._current_run_id = run.id

        repos = self.settings.repo_list
        if not repos:
            logger.warning("No repositories configured")
            self.history.complete_run(run.id, 0, 0, "No repositories configured")
            return

        logger.info(f"Processing {len(repos)} repositories")

        prs_created = 0
        repos_reviewed = 0

        for repo_name in repos:
            if self._shutdown:
                logger.info("Shutdown requested, stopping review")
                break

            if self.deadline.is_past_deadline():
                logger.warning("Deadline reached, stopping review")
                break

            try:
                result = self._process_repo(repo_name)
                repos_reviewed += 1
                if result:
                    prs_created += 1
            except Exception as e:
                logger.error(f"Error processing {repo_name}: {e}")
                self.history.record_pr(
                    run.id,
                    repo_name=repo_name,
                    success=False,
                    error=str(e),
                )

        # Complete the run
        self.history.complete_run(run.id, repos_reviewed, prs_created)
        logger.info(f"Review complete: {repos_reviewed} repos, {prs_created} PRs")

    def _process_repo(self, repo_name: str) -> bool:
        """Process a single repository.

        Args:
            repo_name: Full repository name (owner/repo).

        Returns:
            True if a PR was created.
        """
        logger.info(f"Processing {repo_name}")

        # Clone or pull the repository
        repo_info = self.repo_manager.clone_or_pull(repo_name)
        if not repo_info:
            self.history.record_pr(
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to clone/pull repository",
            )
            return False

        # Get open issues
        issues = self.pr_creator.get_open_issues(repo_name)
        issues = self.issue_analyzer.filter_actionable(issues)
        issues = self.issue_analyzer.prioritize(issues, limit=5)

        # Analyze code
        result = self.code_analyzer.analyze(
            repo_path=repo_info.local_path,
            repo_name=repo_name,
            issues=issues,
        )

        if not result.found_fix or not result.fix:
            logger.info(f"No actionable fix found for {repo_name}")
            self.history.record_pr(
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error="No actionable fixes identified",
            )
            return False

        fix = result.fix
        logger.info(f"Found fix: {fix.pr_title}")

        # Check for existing LucidPulls PR
        if self.pr_creator.has_open_lucidpulls_pr(repo_name):
            logger.info(f"Skipping {repo_name}: existing LucidPulls PR found")
            self.history.record_pr(
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error="Existing LucidPulls PR already open",
            )
            return False

        # Create branch with sanitized file path
        safe_file = sanitize_branch_name(fix.file_path)
        branch_name = f"lucidpulls/{datetime.now().strftime('%Y%m%d')}-{safe_file}"
        if not self.repo_manager.create_branch(repo_info, branch_name):
            self.history.record_pr(
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to create branch",
            )
            return False

        # Apply fix
        if not self.code_analyzer.apply_fix(repo_info.local_path, fix):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                self._current_run_id,
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
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to commit changes",
            )
            return False

        # Push branch
        if not self.repo_manager.push_branch(repo_info, branch_name):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                self._current_run_id,
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
                self._current_run_id,
                repo_name=repo_name,
                success=False,
                error=pr_result.error or "Failed to create PR",
            )
            return False

        # Record success
        self.history.record_pr(
            self._current_run_id,
            repo_name=repo_name,
            pr_number=pr_result.pr_number,
            pr_url=pr_result.pr_url,
            pr_title=fix.pr_title,
            success=True,
        )

        logger.info(f"Created PR #{pr_result.pr_number}: {pr_result.pr_url}")
        return True

    def send_report(self) -> None:
        """Send the morning report notification."""
        logger.info("Preparing morning report")

        # Get the latest completed run
        run = self.history.get_latest_run()
        if not run:
            logger.warning("No review runs found for report")
            return

        # Only send if run completed today
        today = datetime.now(pytz.timezone(self.settings.timezone)).date()
        run_date = run.started_at.date()
        if run_date != today:
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

        # Check LLM availability
        if not self.llm.is_available():
            logger.error(f"LLM provider {self.llm.provider_name} is not available")
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
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown = True
        self.scheduler.stop()


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

    # Create orchestrator
    agent = LucidPulls(settings)

    if args.run_now:
        logger.info("Running review immediately")
        agent.run_review()
    elif args.send_report:
        logger.info("Sending report immediately")
        agent.send_report()
    else:
        # Start the scheduled service
        agent.start()


if __name__ == "__main__":
    main()
