"""login failures journal and unlock

Revision ID: 1033950c4ced
Revises: 9668cc1ec1ac
Create Date: 2026-09-18 19:30:50.459832

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1033950c4ced'
down_revision: Union[str, None] = '9668cc1ec1ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Журнал неудачных входов и источник счётчика блокировки
    # (task_stage2_access п.2.6). Без FK на сотрудника — как reference_changes.
    op.create_table(
        "login_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_login_failures_created_at", "login_failures", ["created_at"])
    op.create_index("ix_login_failures_email_created", "login_failures", ["email", "created_at"])
    op.add_column("employees", sa.Column("login_unlocked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "login_unlocked_at")
    op.drop_index("ix_login_failures_email_created", table_name="login_failures")
    op.drop_index("ix_login_failures_created_at", table_name="login_failures")
    op.drop_table("login_failures")
