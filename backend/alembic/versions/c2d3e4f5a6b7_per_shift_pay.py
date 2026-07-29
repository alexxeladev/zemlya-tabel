"""per-shift pay type: pay_type + shift_rate

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-29

Новый тип оплаты «посменная»: у сотрудника нет месячного оклада, база
начисления = число отработанных смен × фикс-ставка за смену. Существующие
сотрудники — окладники (pay_type='salary'), их расчёт не меняется.
"""
from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("pay_type", sa.String(length=20), nullable=False, server_default="salary"),
    )
    op.add_column("employees", sa.Column("shift_rate", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "shift_rate")
    op.drop_column("employees", "pay_type")
