"""Main entry point and orchestration for LucidPulls."""

import argparse
import contextvars
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz
from github import Auth, Github

from src import setup_logging, current_run_id
from src.utils import sanitize_branch_name
from src.config import load_settings, Settings
from src.database import ReviewHistory
from src.git import RepoManager, PRCreator, GitHubRateLimiter, RateLimitExhausted
from src.llm import get_llm
from src.analyzers import CodeAnalyzer, IssueAnalyzer
from src.models import PRSummary, ReviewReport
from src.notifications import get_notifier
from src.scheduler import ReviewScheduler, DeadlineEnforcer, _write_heartbeat

logger = logging.getLogger("lucidpulls.main")


class LucidPulls:
    """Main orchestrator for the LucidPulls agent."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize LucidPulls.

        Args:
            settings: Optional settings override.
        """
        self.settings = settings or load_settings()

        # Shutdown coordination
        self._shutdown_requested = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._shutdown_event = threading.Event()

        # Single shared GitHub client
        self._github = Github(auth=Auth.Token(self.settings.github_token), timeout=30)
        self._rate_limiter = GitHubRateLimiter(
            self._github, shutdown_event=self._shutdown_event
        )

        # Initialize components
        self.history = ReviewHistory()
        self.repo_manager = RepoManager(
            github=self._github,
            rate_limiter=self._rate_limiter,
            username=self.settings.github_username,
            email=self.settings.github_email,
            ssh_key_path=self.settings.ssh_key_path,
            clone_dir=self.settings.clone_dir,
            max_clone_disk_mb=self.settings.max_clone_disk_mb,
        )
        self.pr_creator = PRCreator(
            github=self._github,
            rate_limiter=self._rate_limiter,
        )

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

    def run_review(self) -> None:
        """Run the nightly review process."""
        logger.info("Starting nightly review")

        # Mark the deadline anchor for this review cycle
        self.deadline.mark_review_started()

        # Start tracking this run
        run_id = self.history.start_run()

        # Set run ID in logging context
        run_id_token = current_run_id.set(str(run_id))

        try:
            # Backup database before processing
            if self.settings.db_backup_enabled:
                self.history.backup_database(self.settings.db_backup_count)

            repos = self.settings.repo_list
            if not repos:
                logger.info("No repositories configured")
                self.history.complete_run(run_id, 0, 0)
                return

            # Clean up stale repo clones
            self.repo_manager.cleanup_stale_repos(repos)

            logger.info(f"Processing {len(repos)} repositories with {self.settings.max_workers} workers")

            prs_created = 0
            repos_reviewed = 0

            with ThreadPoolExecutor(max_workers=self.settings.max_workers) as executor:
                futures = {}
                for repo_name in repos:
                    if self._shutdown_requested.is_set():
                        break
                    if self.deadline.is_past_deadline():
                        logger.warning("Deadline reached, not submitting more repos")
                        break
                    # Each thread needs its own context copy for run_id propagation
                    ctx = contextvars.copy_context()
                    future = executor.submit(ctx.run, self._process_repo, repo_name, run_id)
                    futures[future] = repo_name

                for future in as_completed(futures):
                    if self._shutdown_requested.is_set():
                        logger.info("Shutdown requested, cancelling remaining tasks")
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        break
                    repo_name = futures[future]
                    try:
                        result = future.result()
                        repos_reviewed += 1
                        if result:
                            prs_created += 1
                    except Exception as e:
                        repos_reviewed += 1
                        logger.error(f"Error processing {repo_name}: {e}")

            # Complete the run
            if not self.history.complete_run(run_id, repos_reviewed, prs_created):
                logger.warning(f"Failed to record completion of run #{run_id}")
            logger.info(f"Review complete: {repos_reviewed} repos, {prs_created} PRs")

            # Alert if ALL repos failed (no PRs created and at least one was attempted)
            if repos_reviewed > 0 and prs_created == 0:
                self._send_failure_alert(repos_reviewed)
        finally:
            current_run_id.reset(run_id_token)

    def _process_repo(self, repo_name: str, run_id: int) -> bool:
        """Process a single repository.

        Args:
            repo_name: Full repository name (owner/repo).
            run_id: The current review run ID.

        Returns:
            True if a PR was created.
        """
        logger.info(f"Processing {repo_name}")

        try:
            self._idle.clear()

            # Clone or pull the repository
            try:
                repo_info = self.repo_manager.clone_or_pull(repo_name)
            except RateLimitExhausted as e:
                logger.warning(f"Rate limit exhausted while cloning {repo_name}, skipping")
                self.history.record_pr(
                    run_id,
                    repo_name=repo_name,
                    success=False,
                    error=f"Rate limit exhausted ({e.wait_seconds:.0f}s wait)",
                )
                return False

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
        except Exception as e:
            logger.error(f"Error processing {repo_name}: {e}")
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error=str(e),
            )
            return False
        finally:
            _write_heartbeat()
            self._idle.set()

    def _analyze_and_fix(self, repo_name: str, repo_info: object, run_id: int) -> bool:
        """Run analysis and apply fix for a repo. Separated for resource cleanup.

        Args:
            repo_name: Full repository name (owner/repo).
            repo_info: RepoInfo from clone_or_pull.
            run_id: The current review run ID.

        Returns:
            True if a PR was created.
        """
        # Check for existing LucidPulls PR early (before expensive LLM analysis)
        try:
            if self.pr_creator.has_open_lucidpulls_pr(repo_name):
                logger.info(f"Skipping {repo_name}: existing LucidPulls PR found")
                self.history.record_pr(
                    run_id,
                    repo_name=repo_name,
                    success=False,
                    error="Existing LucidPulls PR already open",
                )
                return False
        except RateLimitExhausted:
            logger.warning(f"Rate limit exhausted checking PRs for {repo_name}, skipping")
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Rate limit exhausted",
            )
            return False

        # Get open issues (used to guide analysis, but not required)
        try:
            issues = self.pr_creator.get_open_issues(repo_name)
        except RateLimitExhausted:
            issues = []
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
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        fix = result.fix
        logger.info(f"Found fix: {fix.pr_title}")

        # Check if this fix was previously rejected
        fix_hash = self._compute_fix_hash(fix)
        if self.history.is_fix_rejected(repo_name, fix.file_path, fix_hash):
            logger.info(f"Skipping previously rejected fix for {repo_name}:{fix.file_path}")
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Fix previously rejected",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

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
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        # Apply fix
        if not self.code_analyzer.apply_fix(repo_info.local_path, fix):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            # Record as rejected so we don't re-suggest the same broken fix
            self.history.record_rejected_fix(
                repo_name, fix.file_path, fix_hash, reason="apply_fix failed"
            )
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to apply fix",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        # Run repo tests to catch regressions (configurable)
        if self.settings.run_tests:
            test_result = self.code_analyzer.run_repo_tests(
                repo_info.local_path, timeout=self.settings.test_timeout
            )
            if test_result.ran and not test_result.passed:
                logger.warning(
                    f"Tests failed after applying fix to {repo_name}: {test_result.detail}"
                )
                # Revert the fix by checking out the original file
                self.repo_manager.cleanup_branch(repo_info, branch_name)
                self.history.record_rejected_fix(
                    repo_name, fix.file_path, fix_hash, reason=f"tests {test_result.status}"
                )
                self.history.record_pr(
                    run_id,
                    repo_name=repo_name,
                    success=False,
                    error=f"Tests {test_result.status} after applying fix",
                    analysis_time=result.analysis_time_seconds,
                    llm_tokens_used=result.llm_tokens_used,
                )
                return False
            if test_result.ran:
                logger.info(f"Tests passed after applying fix to {repo_name}")
            else:
                logger.debug(f"Tests skipped for {repo_name}: {test_result.detail}")

        # Commit changes
        commit_msg = f"{fix.pr_title}\n\n{fix.fix_description}"
        if not self.repo_manager.commit_changes(repo_info, fix.file_path, commit_msg):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to commit changes",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        # Dry-run: log what would happen, clean up, and return
        if self.settings.dry_run:
            logger.info(f"[DRY RUN] Would create PR: {fix.pr_title}")
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                pr_title=fix.pr_title,
                success=True,
                error="dry_run",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
                bug_description=fix.bug_description,
            )
            return True

        # Push branch
        if not self.repo_manager.push_branch(repo_info, branch_name):
            self.repo_manager.cleanup_branch(repo_info, branch_name)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Failed to push branch",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        # Build structured PR body
        pr_body = self._build_pr_body(fix)

        # Create PR
        try:
            pr_result = self.pr_creator.create_pr(
                repo_full_name=repo_name,
                branch_name=branch_name,
                base_branch=repo_info.default_branch,
                title=fix.pr_title,
                body=pr_body,
                related_issue=fix.related_issue,
            )
        except RateLimitExhausted:
            self.repo_manager.cleanup_branch(repo_info, branch_name, remote=True)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error="Rate limit exhausted during PR creation",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
            )
            return False

        if not pr_result.success:
            # Clean up both local and remote branch since push already succeeded
            self.repo_manager.cleanup_branch(repo_info, branch_name, remote=True)
            self.history.record_pr(
                run_id,
                repo_name=repo_name,
                success=False,
                error=pr_result.error or "Failed to create PR",
                analysis_time=result.analysis_time_seconds,
                llm_tokens_used=result.llm_tokens_used,
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
            analysis_time=result.analysis_time_seconds,
            llm_tokens_used=result.llm_tokens_used,
            bug_description=fix.bug_description,
        )

        logger.info(f"Created PR #{pr_result.pr_number}: {pr_result.pr_url}")
        return True

    @staticmethod
    def _build_pr_body(fix: object) -> str:
        """Build a structured PR body from a FixSuggestion.

        Args:
            fix: FixSuggestion with bug/fix details.

        Returns:
            Formatted PR body string.
        """
        sections = [
            "## Summary",
            fix.pr_body,
            "",
            "## Bug",
            fix.bug_description,
            "",
            "## Fix",
            fix.fix_description,
            "",
            f"**File:** `{fix.file_path}`",
            f"**Confidence:** {fix.confidence}",
        ]

        if fix.related_issue:
            sections.append(f"**Related issue:** #{fix.related_issue}")

        # Show the actual code change so reviewers can evaluate from
        # email/mobile notifications without clicking "Files Changed"
        sections.append("")
        sections.append("## Code Changes")
        diff_lines = LucidPulls._format_code_diff(
            fix.original_code, fix.fixed_code
        )
        max_diff_lines = 60
        if len(diff_lines) > max_diff_lines:
            sections.append("```diff")
            sections.extend(diff_lines[:max_diff_lines])
            sections.append(f"... ({len(diff_lines) - max_diff_lines} more lines, see Files Changed)")
            sections.append("```")
        else:
            sections.append("```diff")
            sections.extend(diff_lines)
            sections.append("```")

        sections.extend([
            "",
            "## Review Checklist",
            "- [ ] The fix addresses the described bug",
            "- [ ] No unintended side effects",
            "- [ ] Tests pass (if applicable)",
        ])

        return "\n".join(sections)

    @staticmethod
    def _format_code_diff(original: str, fixed: str) -> list[str]:
        """Format original and fixed code as unified diff lines.

        Args:
            original: The original code snippet.
            fixed: The fixed code snippet.

        Returns:
            List of diff-formatted lines with +/- prefixes.
        """
        import difflib

        original_lines = original.splitlines(keepends=True)
        fixed_lines = fixed.splitlines(keepends=True)
        diff = difflib.unified_diff(
            original_lines, fixed_lines, lineterm=""
        )
        # Skip the --- / +++ / @@ headers from unified_diff, keep the content
        lines = []
        for line in diff:
            # Strip trailing newlines for clean rendering
            lines.append(line.rstrip("\n"))
        return lines

    @staticmethod
    def _compute_fix_hash(fix: object) -> str:
        """Compute a stable hash for a fix to detect duplicates.

        Args:
            fix: FixSuggestion with original_code and fixed_code.

        Returns:
            SHA-256 hex digest of the fix content.
        """
        import hashlib

        content = f"{fix.original_code}\n---\n{fix.fixed_code}"
        return hashlib.sha256(content.encode()).hexdigest()

    def test_notifications(self) -> None:
        """Send a test notification to verify delivery is working."""
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
                    bug_description="Request handler crashes with NullPointerError when optional header is missing",
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
        # run.started_at is typically naive UTC; handle both naive and aware
        if run.started_at.tzinfo is None:
            run_started_local = pytz.utc.localize(run.started_at).astimezone(tz)
        else:
            run_started_local = run.started_at.astimezone(tz)
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

        max_attempts = 3
        retry_delay = 60
        for attempt in range(1, max_attempts + 1):
            result = self.notifier.send_report(report)
            if result.success:
                logger.info(f"Report sent via {self.notifier.channel_name}")
                return
            if attempt < max_attempts:
                logger.warning(
                    f"Notification failed (attempt {attempt}/{max_attempts}): "
                    f"{result.error} — retrying in {retry_delay}s"
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to send report after {max_attempts} attempts: {result.error}"
                )

    def _send_failure_alert(self, repos_reviewed: int) -> None:
        """Send a warning notification when all repos in a run failed.

        Args:
            repos_reviewed: Number of repos that were reviewed.
        """
        if not self.notifier.is_configured():
            return

        logger.warning(f"All {repos_reviewed} repos failed — sending failure alert")

        tz = pytz.timezone(self.settings.timezone)
        now = datetime.now(tz)
        alert_report = ReviewReport(
            date=now,
            repos_reviewed=repos_reviewed,
            prs_created=0,
            prs=[],
            start_time=now,
            end_time=now,
        )

        try:
            self.notifier.send_report(alert_report)
        except Exception as e:
            logger.error(f"Failed to send failure alert: {e}")

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
            key_path = Path(self.settings.ssh_key_path)
            if not key_path.exists():
                logger.error(f"SSH key not found: {key_path}")
                sys.exit(1)
            if not os.access(key_path, os.R_OK):
                logger.error(f"SSH key is not readable: {key_path} — check file permissions (should be 600)")
                sys.exit(1)

        # Validate GitHub token using the shared client
        try:
            self._github.get_user().login
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

        # Start scheduler (blocking — returns when scheduler is stopped)
        self.scheduler.start()

        # Cleanup after scheduler exits (e.g. from signal handler)
        if not self._idle.is_set():
            logger.info("Waiting for in-progress repo operation to finish...")
            self._idle.wait(timeout=60)
        self.close()

    def _signal_handler(self, signum: int, frame: object) -> None:
        """Handle shutdown signals gracefully.

        Keeps work minimal to avoid deadlocks — sets flags and stops the
        scheduler, then lets the main thread in start() handle cleanup.
        """
        logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown_requested.set()
        self._shutdown_event.set()
        self.scheduler.stop()

    def close(self) -> None:
        """Clean up all resources."""
        logger.debug("Closing LucidPulls resources")

        # Close repo manager (git repos, SSH env)
        if hasattr(self, "repo_manager"):
            self.repo_manager.close()

        # Close PR creator (no-op, shared client)
        if hasattr(self, "pr_creator"):
            self.pr_creator.close()

        # Close the shared GitHub client
        if hasattr(self, "_github"):
            self._github.close()

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
        "--dry-run",
        action="store_true",
        help="Analyze and commit locally but skip push and PR creation",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Check heartbeat and exit with 0 (healthy) or 1 (unhealthy)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Health check runs without config — exit early
    if args.health_check:
        from src.scheduler import check_heartbeat
        sys.exit(0 if check_heartbeat() else 1)

    # Load settings once and thread through
    settings = load_settings()
    if args.dry_run:
        settings.dry_run = True
    log_level = "DEBUG" if args.debug else settings.log_level
    setup_logging(log_level, log_format=settings.log_format)

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
