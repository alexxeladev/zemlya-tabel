"""employee weekend pay settings

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-06-11

Per-employee оплата выходных/праздничных часов (правка 3.9-3):
тип оплаты (коэффициент / фиксированная ставка), значение коэффициента,
значение фикс-ставки. По умолчанию — coefficient = 1.5 (старое поведение).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "weekend_pay_type",
            sa.String(length=20),
            nullable=False,
            server_default="coefficient",
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "weekend_coefficient",
            sa.Numeric(4, 2),
            nullable=True,
            server_default="1.5",
        ),
    )
    op.add_column(
        "employees",
        sa.Column("weekend_fixed_rate", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "weekend_fixed_rate")
    op.drop_column("employees", "weekend_coefficient")
    op.drop_column("employees", "weekend_pay_type")
