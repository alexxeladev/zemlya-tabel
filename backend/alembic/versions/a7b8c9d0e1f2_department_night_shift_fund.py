"""department night shift fund (месячный фонд ночных смен отдела)

task_night_shifts_rework: ставка ночной смены больше не задаётся руками, она
ВЫЧИСЛЯЕТСЯ из фонда отдела (фонд / календарные дни месяца), и тот же фонд
ограничивает суммарное число ночных смен отдела за месяц.

Существующим отделам проставляется дефолт 100 000 — то самое значение, которое
раньше подразумевалось как «лимит», но нигде не настраивалось.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

DEFAULT_FUND = "100000"


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column(
            "night_shift_fund",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=DEFAULT_FUND,
        ),
    )


def downgrade() -> None:
    op.drop_column("departments", "night_shift_fund")
