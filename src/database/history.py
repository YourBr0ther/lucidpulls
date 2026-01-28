"""Review history tracking and database operations."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, joinedload

from src.database.models import Base, ReviewRun, PRRecord
from src.notifications.base import PRSummary, ReviewReport

logger = logging.getLogger("lucidpulls.database.history")


class ReviewHistory:
    """Manages review history in SQLite database."""

    def __init__(self, db_path: str = "data/lucidpulls.db"):
        """Initialize review history.

        Args:
            db_path: Path to SQLite database file.
        """
        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url, echo=False)
        # expire_on_commit=False prevents detached instance errors when accessing
        # ORM objects after the session closes
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Create tables if they don't exist
        Base.metadata.create_all(self.engine)
        logger.debug(f"Database initialized at {db_path}")

    def start_run(self) -> int:
        """Start a new review run.

        Returns:
            The ID of the created ReviewRun record.
        """
        with self.SessionLocal() as session:
            run = ReviewRun(
                started_at=datetime.now(timezone.utc),
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
        error: Optional[str] = None,
    ) -> None:
        """Complete a review run.

        Args:
            run_id: Review run ID.
            repos_reviewed: Number of repos reviewed.
            prs_created: Number of PRs created.
            error: Optional error message if run failed.
        """
        try:
            with self.SessionLocal() as session:
                run = session.query(ReviewRun).filter(ReviewRun.id == run_id).first()
                if run:
                    run.completed_at = datetime.now(timezone.utc)
                    run.repos_reviewed = repos_reviewed
                    run.prs_created = prs_created
                    run.status = "failed" if error else "completed"
                    run.error = error
                    session.commit()

                    logger.info(f"Completed review run #{run_id}: {run.status}")
        except Exception as e:
            logger.error(f"Failed to complete run #{run_id}: {e}")

    def record_pr(
        self,
        run_id: int,
        repo_name: str,
        pr_number: Optional[int] = None,
        pr_url: Optional[str] = None,
        pr_title: Optional[str] = None,
        success: bool = False,
        error: Optional[str] = None,
    ) -> None:
        """Record a PR creation result.

        Args:
            run_id: Review run ID.
            repo_name: Repository name.
            pr_number: PR number if created.
            pr_url: PR URL if created.
            pr_title: PR title if created.
            success: Whether PR was created successfully.
            error: Error message if failed.
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
                )
                session.add(record)
                session.commit()

                status = "success" if success else "skipped"
                logger.debug(f"Recorded PR for {repo_name}: {status}")
        except Exception as e:
            logger.error(f"Failed to record PR for {repo_name}: {e}")

    def get_run(self, run_id: int) -> Optional[ReviewRun]:
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

    def get_latest_run(self) -> Optional[ReviewRun]:
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

    def build_report(self, run_id: int) -> Optional[ReviewReport]:
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
                )
                for pr in prs
            ]

            return ReviewReport(
                date=run.started_at,
                repos_reviewed=run.repos_reviewed,
                prs_created=run.prs_created,
                prs=summaries,
                start_time=run.started_at,
                end_time=run.completed_at or datetime.now(timezone.utc),
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
                .options(joinedload(ReviewRun.prs))
                .order_by(ReviewRun.started_at.desc())
                .limit(limit)
                .all()
            )

    def close(self) -> None:
        """Close the database engine and release connections."""
        if hasattr(self, "engine"):
            self.engine.dispose()
            logger.debug("Database engine disposed")
