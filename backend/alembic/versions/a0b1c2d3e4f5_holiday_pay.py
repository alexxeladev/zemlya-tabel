"""holiday pay: отдельная оплата работы в праздничный день

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-29

Работа в нерабочий праздничный день выделена в самостоятельную категорию,
отдельную от «вне графика» (выход в свой выходной). Настройки per-employee
повторяют механизм выходных, но дефолт коэффициента 2.0 — ТК требует за
праздник не менее двойной оплаты.
"""
from alembic import op
import sqlalchemy as sa


revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "holiday_pay_type",
            sa.String(length=20),
            nullable=False,
            server_default="coefficient",
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "holiday_coefficient",
            sa.Numeric(4, 2),
            nullable=True,
            server_default="2",
        ),
    )
    op.add_column(
        "employees",
        sa.Column("holiday_fixed_rate", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "holiday_fixed_rate")
    op.drop_column("employees", "holiday_coefficient")
    op.drop_column("employees", "holiday_pay_type")
