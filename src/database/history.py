"""Review history tracking and database operations."""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import joinedload, sessionmaker, subqueryload

from src.database.models import PRRecord, RejectedFix, ReviewRun
from src.models import PRSummary, ReviewReport

logger = logging.getLogger("lucidpulls.database.history")


class ReviewHistory:
    """Manages review history in SQLite database."""

    def __init__(self, db_path: str = "data/lucidpulls.db"):
        """Initialize review history.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url, echo=False)

        # Enable WAL mode for better crash recovery and concurrent reads,
        # and set a busy timeout so concurrent ThreadPoolExecutor workers
        # retry on write contention instead of immediately failing.
        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        # expire_on_commit=False prevents detached instance errors when accessing
        # ORM objects after the session closes
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Run Alembic migrations (handles fresh, stamped, and upgrade cases)
        self._run_migrations()
        logger.debug(f"Database initialized at {db_path}")

    def _run_migrations(self) -> None:
        """Run Alembic migrations to ensure schema is up to date.

        Handles three cases:
        - Fresh DB: runs all migrations from scratch.
        - Existing DB with alembic_version: normal upgrade to head.
        - Existing DB without alembic_version: stamps at 0001, then upgrades.
        """
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import inspect

        try:
            alembic_cfg = Config()
            migrations_dir = str(Path(__file__).parent.parent.parent / "migrations")
            alembic_cfg.set_main_option("script_location", migrations_dir)
            alembic_cfg.set_main_option("sqlalchemy.url", self.db_url)
            alembic_cfg.attributes["engine"] = self.engine

            inspector = inspect(self.engine)
            tables = inspector.get_table_names()
            if tables and "alembic_version" not in tables:
                # Existing DB created before Alembic was added — stamp current revision
                command.stamp(alembic_cfg, "0001")

            command.upgrade(alembic_cfg, "head")
        except Exception as e:
            logger.error(f"Database migration failed: {e}")
            raise RuntimeError(
                f"Failed to run database migrations: {e}. "
                f"Check database at {self.db_path} for corruption."
            ) from e

    def backup_database(self, backup_count: int = 7) -> str | None:
        """Create a backup of the database using SQLite's backup API.

        Args:
            backup_count: Number of recent backups to keep.

        Returns:
            Path to the backup file, or None on failure.
        """
        try:
            backup_dir = Path(self.db_path).parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"lucidpulls_{timestamp}.db"

            # Use sqlite3 backup API for a consistent snapshot
            source = sqlite3.connect(self.db_path)
            dest = sqlite3.connect(str(backup_path))
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()

            logger.info(f"Database backup created: {backup_path}")

            # Rotate: keep only the N most recent backups
            backups = sorted(backup_dir.glob("lucidpulls_*.db"))
            for old_backup in backups[:-backup_count]:
                try:
                    old_backup.unlink()
                    logger.debug(f"Deleted old backup: {old_backup}")
                except OSError as e:
                    logger.warning(f"Failed to delete old backup {old_backup}: {e}")

            return str(backup_path)
        except Exception as e:
            logger.error(f"Database backup failed: {e}")
            return None

    def start_run(self) -> int:
        """Start a new review run.

        Returns:
            The ID of the created ReviewRun record.
        """
        with self.SessionLocal() as session:
            run = ReviewRun(
                started_at=datetime.now(UTC),
                status="running",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

            logger.info(f"Started review run #{run_id}")
            return run_id

    def complete_run(
        self,
        run_id: int,
        repos_reviewed: int,
        prs_created: int,
        error: str | None = None,
    ) -> bool:
        """Complete a review run.

        Args:
            run_id: Review run ID.
            repos_reviewed: Number of repos reviewed.
            prs_created: Number of PRs created.
            error: Optional error message if run failed.

        Returns:
            True if the database write succeeded.
        """
        try:
            with self.SessionLocal() as session:
                run = session.query(ReviewRun).filter(ReviewRun.id == run_id).first()
                if run:
                    run.completed_at = datetime.now(UTC)
                    run.repos_reviewed = repos_reviewed
                    run.prs_created = prs_created
                    run.status = "failed" if error else "completed"
                    run.error = error
                    session.commit()

                    logger.info(f"Completed review run #{run_id}: {run.status}")
            return True
        except Exception as e:
            logger.error(f"Failed to complete run #{run_id}: {e}")
            return False

    def record_pr(
        self,
        run_id: int,
        repo_name: str,
        pr_number: int | None = None,
        pr_url: str | None = None,
        pr_title: str | None = None,
        success: bool = False,
        error: str | None = None,
        analysis_time: float | None = None,
        llm_tokens_used: int | None = None,
        bug_description: str | None = None,
    ) -> bool:
        """Record a PR creation result.

        Args:
            run_id: Review run ID.
            repo_name: Repository name.
            pr_number: PR number if created.
            pr_url: PR URL if created.
            pr_title: PR title if created.
            success: Whether PR was created successfully.
            error: Error message if failed.
            analysis_time: Time spent on analysis in seconds.
            llm_tokens_used: Number of LLM tokens consumed.
            bug_description: Short description of the bug found.

        Returns:
            True if the database write succeeded.
        """
        try:
            with self.SessionLocal() as session:
                record = PRRecord(
                    review_run_id=run_id,
                    repo_name=repo_name,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    pr_title=pr_title,
                    success=success,
                    error=error,
                    analysis_time=analysis_time,
                    llm_tokens_used=llm_tokens_used,
                    bug_description=bug_description,
                )
                session.add(record)
                session.commit()

                status = "success" if success else "skipped"
                logger.debug(f"Recorded PR for {repo_name}: {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to record PR for {repo_name}: {e}")
            return False

    def get_run(self, run_id: int) -> ReviewRun | None:
        """Get a specific review run by ID.

        Args:
            run_id: Review run ID.

        Returns:
            ReviewRun if found, None otherwise.
        """
        with self.SessionLocal() as session:
            return (
                session.query(ReviewRun)
                .options(joinedload(ReviewRun.prs))
                .filter(ReviewRun.id == run_id)
                .first()
            )

    def get_latest_run(self) -> ReviewRun | None:
        """Get the most recent review run.

        Returns:
            Most recent ReviewRun if any.
        """
        with self.SessionLocal() as session:
            return (
                session.query(ReviewRun)
                .options(joinedload(ReviewRun.prs))
                .order_by(ReviewRun.started_at.desc())
                .first()
            )

    def get_run_prs(self, run_id: int) -> list[PRRecord]:
        """Get all PR records for a run.

        Args:
            run_id: Review run ID.

        Returns:
            List of PRRecord objects.
        """
        with self.SessionLocal() as session:
            return (
                session.query(PRRecord)
                .filter(PRRecord.review_run_id == run_id)
                .all()
            )

    def build_report(self, run_id: int) -> ReviewReport | None:
        """Build a review report from a run.

        Args:
            run_id: Review run ID.

        Returns:
            ReviewReport if run exists.
        """
        with self.SessionLocal() as session:
            run = session.query(ReviewRun).filter(ReviewRun.id == run_id).first()
            if not run:
                return None

            prs = (
                session.query(PRRecord)
                .filter(PRRecord.review_run_id == run_id)
                .all()
            )

            summaries = [
                PRSummary(
                    repo_name=pr.repo_name,
                    pr_number=pr.pr_number,
                    pr_url=pr.pr_url,
                    pr_title=pr.pr_title,
                    success=pr.success,
                    error=pr.error,
                    bug_description=pr.bug_description,
                )
                for pr in prs
            ]

            # Sum LLM tokens across all PR records for this run
            token_values = [pr.llm_tokens_used for pr in prs if pr.llm_tokens_used is not None]
            total_tokens = sum(token_values) if token_values else None

            return ReviewReport(
                date=run.started_at,
                repos_reviewed=run.repos_reviewed,
                prs_created=run.prs_created,
                prs=summaries,
                start_time=run.started_at,
                end_time=run.completed_at or datetime.now(UTC),
                llm_tokens_used=total_tokens,
            )

    def get_recent_runs(self, limit: int = 10) -> list[ReviewRun]:
        """Get recent review runs.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            List of recent ReviewRun objects.
        """
        with self.SessionLocal() as session:
            return (
                session.query(ReviewRun)
                .options(subqueryload(ReviewRun.prs))
                .order_by(ReviewRun.started_at.desc())
                .limit(limit)
                .all()
            )

    def is_fix_rejected(self, repo_name: str, file_path: str, fix_hash: str) -> bool:
        """Check if a fix has been previously rejected.

        Args:
            repo_name: Full repository name (owner/repo).
            file_path: Path to the file within the repo.
            fix_hash: SHA-256 hash of original_code + fixed_code.

        Returns:
            True if this fix was previously rejected.
        """
        try:
            with self.SessionLocal() as session:
                match = (
                    session.query(RejectedFix)
                    .filter(
                        RejectedFix.repo_name == repo_name,
                        RejectedFix.file_path == file_path,
                        RejectedFix.fix_hash == fix_hash,
                    )
                    .first()
                )
                return match is not None
        except Exception as e:
            logger.error(f"Failed to check rejected fixes: {e}")
            return False

    def record_rejected_fix(
        self,
        repo_name: str,
        file_path: str,
        fix_hash: str,
        reason: str | None = None,
    ) -> bool:
        """Record a fix as rejected so it won't be re-suggested.

        Args:
            repo_name: Full repository name (owner/repo).
            file_path: Path to the file within the repo.
            fix_hash: SHA-256 hash of original_code + fixed_code.
            reason: Optional reason for rejection.

        Returns:
            True if the database write succeeded.
        """
        try:
            with self.SessionLocal() as session:
                record = RejectedFix(
                    repo_name=repo_name,
                    file_path=file_path,
                    fix_hash=fix_hash,
                    reason=reason,
                    created_at=datetime.now(UTC),
                )
                session.add(record)
                session.commit()
                logger.debug(f"Recorded rejected fix for {repo_name}:{file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to record rejected fix: {e}")
            return False

    def close(self) -> None:
        """Close the database engine and release connections."""
        if hasattr(self, "engine"):
            self.engine.dispose()
            logger.debug("Database engine disposed")
