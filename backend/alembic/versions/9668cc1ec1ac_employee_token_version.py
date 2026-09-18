"""employee token version

Revision ID: 9668cc1ec1ac
Revises: b6c7d8e9f0a1
Create Date: 2026-09-18 19:09:46.332517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9668cc1ec1ac'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Версия сессий (task_stage2_access п.2.4). server_default 0 совпадает с
    # тем, как читается токен без claim-а `ver`: выкатка никого не разлогинивает.
    op.add_column(
        "employees",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("employees", "token_version")
