"""Initial schema for review_runs and pr_records.

Revision ID: 0001
Revises:
Create Date: 2026-02-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("repos_reviewed", sa.Integer, default=0),
        sa.Column("prs_created", sa.Integer, default=0),
        sa.Column("status", sa.String(50), default="running"),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_table(
        "pr_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("review_run_id", sa.Integer, sa.ForeignKey("review_runs.id"), nullable=False),
        sa.Column("repo_name", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=True),
        sa.Column("pr_url", sa.String(500), nullable=True),
        sa.Column("pr_title", sa.String(500), nullable=True),
        sa.Column("success", sa.Boolean, default=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("analysis_time", sa.Float, nullable=True),
        sa.Column("llm_tokens_used", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pr_records")
    op.drop_table("review_runs")
