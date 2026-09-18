"""employee_positions.job_title_id: рабочее место охраны ссылается на должность по ключу

Revision ID: 9b2c3d4e5f6a
Revises: 8a1b2c3d4e5f
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9b2c3d4e5f6a"
down_revision: Union[str, None] = "8a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employee_positions",
        sa.Column("job_title_id", sa.Integer(), sa.ForeignKey("guard_job_titles.id"), nullable=True),
    )
    op.create_index("ix_employee_positions_job_title_id", "employee_positions", ["job_title_id"])
    # Бэкфилл по названию — один раз, здесь: до этой миграции должность рабочего
    # места охраны узнавалась только по `title`. Позиции обычных отделов не
    # трогаем, даже если названы «Охранник».
    op.execute(sa.text("""
        update employee_positions p
           set job_title_id = t.id
          from guard_job_titles t, departments d
         where p.department_id = d.id
           and d.is_guard_department
           and lower(trim(p.title)) = lower(t.name)
    """))


def downgrade() -> None:
    op.drop_index("ix_employee_positions_job_title_id", table_name="employee_positions")
    op.drop_column("employee_positions", "job_title_id")
