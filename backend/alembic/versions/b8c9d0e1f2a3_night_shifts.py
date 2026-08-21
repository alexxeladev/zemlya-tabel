"""night shifts: отметки выходов в ночь + снятие ручной ставки с позиции

task_night_shifts_rework. Ночная смена становится отдельной сущностью —
отметкой (позиция, дата) без часов: она не привязана к графику и сосуществует
с дневными часами того же дня.

Ручная `employee_positions.night_rate` снимается: цена смены теперь одна и
вычисляется из фонда отдела (фонд / календарные дни месяца). Два источника
цены неминуемо разошлись бы, поэтому колонка удаляется, а не остаётся
«на всякий случай». Downgrade её возвращает пустой — прежние значения не
восстанавливаются, они больше ни на что не влияли (в расчёте не участвовали).

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "night_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["employee_positions.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_id", "work_date", name="uq_night_shift_position_date"
        ),
    )
    op.create_index("ix_night_shifts_employee_id", "night_shifts", ["employee_id"])
    op.create_index("ix_night_shifts_position_id", "night_shifts", ["position_id"])
    op.create_index("ix_night_shifts_work_date", "night_shifts", ["work_date"])
    # Лимит фонда считается по отделу за месяц — то есть выборкой по позициям
    # и диапазону дат.
    op.create_index(
        "ix_night_shift_position_date", "night_shifts", ["position_id", "work_date"]
    )

    op.drop_column("employee_positions", "night_rate")


def downgrade() -> None:
    op.add_column(
        "employee_positions",
        sa.Column("night_rate", sa.Numeric(12, 2), nullable=True),
    )
    op.drop_index("ix_night_shift_position_date", table_name="night_shifts")
    op.drop_index("ix_night_shifts_work_date", table_name="night_shifts")
    op.drop_index("ix_night_shifts_position_id", table_name="night_shifts")
    op.drop_index("ix_night_shifts_employee_id", table_name="night_shifts")
    op.drop_table("night_shifts")
