"""timesheet entry version for optimistic locking

Revision ID: 7703219dafc1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-17 19:53:22.189722

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7703219dafc1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Версия ячейки табеля для оптимистичной блокировки (task_stage1 п.1.5).
    # server_default проставляет 1 всем существующим строкам и вставкам мимо ORM.
    op.add_column(
        "timesheet_entries",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("timesheet_entries", "version")
