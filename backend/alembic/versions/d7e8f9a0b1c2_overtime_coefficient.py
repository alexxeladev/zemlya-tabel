"""employee overtime coefficient

Revision ID: d7e8f9a0b1c2
Revises: 685b31c8afcc
Create Date: 2026-06-26

Коэффициент переработки per-employee (задача 3.11b п.0). Переработка считается
помесячно: (оклад/норма) × часы_переработки × коэффициент (0 / 1 / 1.5).
По умолчанию 1.5 (старое поведение).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "685b31c8afcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "overtime_coefficient",
            sa.Numeric(4, 2),
            nullable=True,
            server_default="1.5",
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "overtime_coefficient")
