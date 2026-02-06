"""Add rejected_fixes table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    if "rejected_fixes" not in inspector.get_table_names():
        op.create_table(
            "rejected_fixes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("repo_name", sa.String(255), nullable=False),
            sa.Column("file_path", sa.String(500), nullable=False),
            sa.Column("fix_hash", sa.String(64), nullable=False),
            sa.Column("reason", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_rejected_fixes_lookup",
            "rejected_fixes",
            ["repo_name", "file_path", "fix_hash"],
        )


def downgrade() -> None:
    op.drop_index("ix_rejected_fixes_lookup", table_name="rejected_fixes")
    op.drop_table("rejected_fixes")
