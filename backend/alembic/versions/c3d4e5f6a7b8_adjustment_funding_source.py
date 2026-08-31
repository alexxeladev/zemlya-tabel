"""adjustment funding source (task_funding_source)

Источник финансирования премии/KPI — юрлицо, которое эти деньги оплачивает.
Сумма с источником целиком относится на его затраты, а база каскада
распределения уменьшается на неё (см. CLAUDE.md, «Источник финансирования»).

Поле необязательное и по умолчанию пустое: после деплоя ни одно существующее
начисление источника не имеет, и распределение всех месяцев остаётся прежним.

Revision ID: c3d4e5f6a7b8
Revises: a4b5c6d7e8f9
"""
import sqlalchemy as sa

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee_adjustments",
        sa.Column("funding_company_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_employee_adjustments_funding_company_id",
        "employee_adjustments",
        ["funding_company_id"],
    )
    op.create_foreign_key(
        "fk_employee_adjustments_funding_company",
        "employee_adjustments",
        "companies",
        ["funding_company_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_employee_adjustments_funding_company",
        "employee_adjustments",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_employee_adjustments_funding_company_id",
        table_name="employee_adjustments",
    )
    op.drop_column("employee_adjustments", "funding_company_id")
