"""Add bug_description column to pr_records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("pr_records")]
    if "bug_description" not in columns:
        op.add_column("pr_records", sa.Column("bug_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pr_records", "bug_description")
