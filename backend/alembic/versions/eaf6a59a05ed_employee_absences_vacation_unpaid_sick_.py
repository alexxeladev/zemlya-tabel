"""employee absences (vacation/unpaid/sick/absent)

Revision ID: eaf6a59a05ed
Revises: 4252b2d438c9
Create Date: 2026-07-27 22:12:59.076700

Только создание таблицы отсутствий. Шум автогенерации (TIMESTAMP→String у
created_at/updated_at и снос partial unique index-ов периодов) убран вручную —
это расхождение аннотаций моделей с реальной схемой, а не изменение схемы.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'eaf6a59a05ed'
down_revision: Union[str, None] = '4252b2d438c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'employee_absences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('work_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "kind IN ('vacation', 'unpaid', 'sick', 'absent')", name='ck_absence_kind'
        ),
        sa.ForeignKeyConstraint(['created_by_id'], ['employees.id'], ),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('employee_id', 'work_date', name='uq_absence_employee_date'),
    )
    op.create_index(
        'ix_absence_employee_date', 'employee_absences', ['employee_id', 'work_date'],
        unique=False,
    )
    op.create_index(
        op.f('ix_employee_absences_employee_id'), 'employee_absences', ['employee_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_employee_absences_work_date'), 'employee_absences', ['work_date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_employee_absences_work_date'), table_name='employee_absences')
    op.drop_index(op.f('ix_employee_absences_employee_id'), table_name='employee_absences')
    op.drop_index('ix_absence_employee_date', table_name='employee_absences')
    op.drop_table('employee_absences')
