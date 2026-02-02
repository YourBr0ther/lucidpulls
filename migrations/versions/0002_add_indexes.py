"""Add indexes for common query columns.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-02
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_existing_indexes(connection) -> set[str]:
    """Get set of existing index names across all tables."""
    inspector = sa_inspect(connection)
    names: set[str] = set()
    for table in inspector.get_table_names():
        for idx in inspector.get_indexes(table):
            if idx["name"]:
                names.add(idx["name"])
    return names


def upgrade() -> None:
    conn = op.get_bind()
    existing = _get_existing_indexes(conn)

    indexes = [
        ("ix_pr_records_review_run_id", "pr_records", ["review_run_id"]),
        ("ix_pr_records_repo_name", "pr_records", ["repo_name"]),
        ("ix_pr_records_created_at", "pr_records", ["created_at"]),
        ("ix_review_runs_started_at", "review_runs", ["started_at"]),
        ("ix_review_runs_status", "review_runs", ["status"]),
    ]
    for name, table, columns in indexes:
        if name not in existing:
            op.create_index(name, table, columns)


def downgrade() -> None:
    op.drop_index("ix_review_runs_status", "review_runs")
    op.drop_index("ix_review_runs_started_at", "review_runs")
    op.drop_index("ix_pr_records_created_at", "pr_records")
    op.drop_index("ix_pr_records_repo_name", "pr_records")
    op.drop_index("ix_pr_records_review_run_id", "pr_records")
