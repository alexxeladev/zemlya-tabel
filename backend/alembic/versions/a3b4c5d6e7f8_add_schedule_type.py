"""add schedule_type to schedules

Revision ID: a3b4c5d6e7f8
Revises: 2ebe9fa53315
Create Date: 2026-06-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = '2ebe9fa53315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'schedules',
        sa.Column('schedule_type', sa.String(20), nullable=False, server_default='standard'),
    )


def downgrade() -> None:
    op.drop_column('schedules', 'schedule_type')
