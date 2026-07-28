"""shift schedules: weekday/cyclic types, work_weekdays, cycle anchor

Revision ID: f9a0b1c2d3e4
Revises: eaf6a59a05ed
Create Date: 2026-07-28

task_shift_schedules: график знает свои рабочие дни явно —
weekday-график хранит набор дней недели, cyclic-график — дату начала цикла
и паттерн (смен подряд / выходных подряд). Значения schedule_type
переименованы: standard → weekday, shift → cyclic.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f9a0b1c2d3e4"
down_revision = "eaf6a59a05ed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column("work_weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("schedules", sa.Column("cycle_start_date", sa.Date(), nullable=True))
    op.add_column("schedules", sa.Column("cycle_work_days", sa.Integer(), nullable=True))
    op.add_column("schedules", sa.Column("cycle_off_days", sa.Integer(), nullable=True))

    op.execute("UPDATE schedules SET schedule_type = 'weekday' WHERE schedule_type = 'standard'")
    op.execute("UPDATE schedules SET schedule_type = 'cyclic' WHERE schedule_type = 'shift'")
    op.alter_column(
        "schedules",
        "schedule_type",
        existing_type=sa.String(length=20),
        server_default="weekday",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "schedules",
        "schedule_type",
        existing_type=sa.String(length=20),
        server_default="standard",
        existing_nullable=False,
    )
    op.execute("UPDATE schedules SET schedule_type = 'standard' WHERE schedule_type = 'weekday'")
    op.execute("UPDATE schedules SET schedule_type = 'shift' WHERE schedule_type = 'cyclic'")

    op.drop_column("schedules", "cycle_off_days")
    op.drop_column("schedules", "cycle_work_days")
    op.drop_column("schedules", "cycle_start_date")
    op.drop_column("schedules", "work_weekdays")
