"""manager ↔ departments many-to-many (managed departments)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-30

Менеджер может руководить несколькими отделами (task_org_structure ч.2).
`employees.department_id` (где сотрудник работает) НЕ трогаем — добавляем
отдельную связь «чем руководит».

Backfill: каждому активному менеджеру его текущий department_id переносится
в новую таблицу, иначе после релиза он потеряет доступ к своему отделу.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_managers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("department_id", "employee_id", name="uq_department_manager"),
    )
    op.create_index(
        "ix_department_managers_department_id", "department_managers", ["department_id"]
    )
    op.create_index(
        "ix_department_managers_employee_id", "department_managers", ["employee_id"]
    )

    # Сохранить доступ существующим менеджерам: их отдел → управляемый отдел.
    op.execute(
        """
        INSERT INTO department_managers (department_id, employee_id)
        SELECT department_id, id
        FROM employees
        WHERE role = 'manager' AND department_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_department_managers_employee_id", table_name="department_managers")
    op.drop_index("ix_department_managers_department_id", table_name="department_managers")
    op.drop_table("department_managers")
