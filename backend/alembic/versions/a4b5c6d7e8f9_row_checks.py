"""row_checks: личная отметка «строку проверил» (task_pilot_ux ч.3)

Ключ (user_id, position_id, year, month): отметка личная и месячная —
в новом месяце строк просто нет, переносить и сбрасывать нечего.

Автогенерация тянет за собой посторонний «дрейф» (TIMESTAMP → String у
колонок с TypeDecorator, partial-индексы периодов) — в миграции оставлена
ТОЛЬКО новая таблица.

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "row_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["position_id"], ["employee_positions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "position_id", "year", "month", name="uq_row_check_user_pos_month"
        ),
    )
    # Выдача табеля берёт отметки одним запросом: «мои за этот месяц».
    op.create_index(
        "ix_row_check_user_month", "row_checks", ["user_id", "year", "month"]
    )
    op.create_index("ix_row_checks_position_id", "row_checks", ["position_id"])
    op.create_index("ix_row_checks_user_id", "row_checks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_row_checks_user_id", table_name="row_checks")
    op.drop_index("ix_row_checks_position_id", table_name="row_checks")
    op.drop_index("ix_row_check_user_month", table_name="row_checks")
    op.drop_table("row_checks")
