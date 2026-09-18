"""dashboard month cache + data versions

Revision ID: b6c7d8e9f0a1
Revises: 9b2c3d4e5f6a
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "9b2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_versions",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "dashboard_month_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("month_version", sa.Integer(), nullable=False),
        sa.Column("reference_version", sa.Integer(), nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("year", "month", name="uq_dashboard_month_cache"),
    )


def downgrade() -> None:
    op.drop_table("dashboard_month_cache")
    op.drop_table("data_versions")
