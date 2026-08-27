"""company sort_order and short_name

Настраиваемый порядок перечисления юрлиц (task_vedomost_format ч.1) и короткое
название для узких колонок (ч.2).

Дефолт порядка — `sort_order = id`: текущий порядок выдачи (он был по id)
сохраняется один в один, ни один экран после миграции не меняется. Нужный
порядок админ выставляет в справочнике руками.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
import sqlalchemy as sa

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("short_name", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.execute("UPDATE companies SET sort_order = id")


def downgrade() -> None:
    op.drop_column("companies", "sort_order")
    op.drop_column("companies", "short_name")
