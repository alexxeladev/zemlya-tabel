"""employment period dates on position (hire/dismissal)

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f7
Create Date: 2026-09-16

Период работы РАБОЧЕГО МЕСТА (task_employment_period): табель заполняется
только от даты приёма до даты увольнения включительно.

Даты нужны именно на позиции: у совместителя одна работа может быть закрыта, а
вторая продолжаться. Даты человека (`employees.hire_date` / `.dismissal_date`)
остаются на месте и работают как ВНЕШНЯЯ граница — границы позиции считаются
пересечением, см. `app.services.employment_period`.

Обе колонки nullable и данными НЕ заполняются: пустая дата означает отсутствие
границы, поэтому после применения миграции поведение для всех существующих
рабочих мест не меняется ни на день. Ограничение по датам человека при этом
начинает действовать сразу — оно берётся из уже заполненных колонок `employees`.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee_positions", sa.Column("hire_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "employee_positions", sa.Column("dismissal_date", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("employee_positions", "dismissal_date")
    op.drop_column("employee_positions", "hire_date")
