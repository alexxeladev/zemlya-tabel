"""applications split into in_progress/closed (task_hr_applications)

В исходном файле HR заявки показаны тремя строками: «в работе», «закрытые» и
«Заявок» = их сумма. Храним две части, общее число считаем — иначе «Заявок» и
разбивка живут отдельно и рано или поздно перестанут сходиться.

Распределение по-прежнему считается от ОБЩЕГО числа (в работе + закрытые).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
import sqlalchemy as sa

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "department_applications",
        sa.Column("in_progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "department_applications",
        sa.Column("closed", sa.Integer(), nullable=False, server_default="0"),
    )
    # Ранее введённые заявки — это общее число; считаем их «в работе», иначе
    # сумма (in_progress + closed) поехала бы относительно того, что видел HR.
    op.execute("UPDATE department_applications SET in_progress = count")
    op.drop_column("department_applications", "count")


def downgrade() -> None:
    op.add_column(
        "department_applications",
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE department_applications SET count = in_progress + closed")
    op.drop_column("department_applications", "closed")
    op.drop_column("department_applications", "in_progress")
