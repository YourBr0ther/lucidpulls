"""SQLAlchemy database models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class ReviewRun(Base):
    """Record of a nightly review run."""

    __tablename__ = "review_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    repos_reviewed = Column(Integer, default=0)
    prs_created = Column(Integer, default=0)
    status = Column(String(50), default="running")  # running, completed, failed
    error = Column(Text, nullable=True)

    # Relationship to PR records
    prs = relationship("PRRecord", back_populates="review_run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ReviewRun(id={self.id}, status={self.status}, prs={self.prs_created})>"


class PRRecord(Base):
    """Record of a created pull request."""

    __tablename__ = "pr_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    review_run_id = Column(Integer, ForeignKey("review_runs.id"), nullable=False)
    repo_name = Column(String(255), nullable=False)
    pr_number = Column(Integer, nullable=True)
    pr_url = Column(String(500), nullable=True)
    pr_title = Column(String(500), nullable=True)
    success = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationship back to review run
    review_run = relationship("ReviewRun", back_populates="prs")

    def __repr__(self) -> str:
        status = "success" if self.success else "failed"
        return f"<PRRecord(repo={self.repo_name}, pr=#{self.pr_number}, {status})>"
