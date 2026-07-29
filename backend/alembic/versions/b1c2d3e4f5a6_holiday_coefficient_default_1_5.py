"""holiday_coefficient: дефолт 1.5 вместо 2

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-29

Коэффициента ×2 в компании нет — праздничные по умолчанию оплачиваются
полуторным, как и выходные; точная ставка задаётся в карточке сотрудника.
Предыдущая миграция проставила существующим сотрудникам 2 (никто это
значение осознанно не выбирал — колонка появилась вместе с ней), поэтому
переводим такие строки на 1.5.
"""
from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "employees",
        "holiday_coefficient",
        existing_type=sa.Numeric(4, 2),
        server_default="1.5",
        existing_nullable=True,
    )
    op.execute("UPDATE employees SET holiday_coefficient = 1.5 WHERE holiday_coefficient = 2")


def downgrade() -> None:
    op.execute("UPDATE employees SET holiday_coefficient = 2 WHERE holiday_coefficient = 1.5")
    op.alter_column(
        "employees",
        "holiday_coefficient",
        existing_type=sa.Numeric(4, 2),
        server_default="2",
        existing_nullable=True,
    )
