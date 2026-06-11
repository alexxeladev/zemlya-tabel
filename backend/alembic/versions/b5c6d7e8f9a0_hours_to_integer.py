"""timesheet hours: numeric -> integer

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-06-08

Округляет существующие дробные часы и меняет тип колонки на INTEGER.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ROUND existing values and change type to integer in one ALTER via USING.
    op.alter_column(
        "timesheet_entries",
        "hours",
        type_=sa.Integer(),
        existing_type=sa.Numeric(4, 2),
        existing_nullable=False,
        postgresql_using="ROUND(hours)::integer",
    )


def downgrade() -> None:
    op.alter_column(
        "timesheet_entries",
        "hours",
        type_=sa.Numeric(4, 2),
        existing_type=sa.Integer(),
        existing_nullable=False,
        postgresql_using="hours::numeric(4,2)",
    )
