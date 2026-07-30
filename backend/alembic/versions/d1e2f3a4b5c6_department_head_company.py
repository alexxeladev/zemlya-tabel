"""department head company (org structure tree grouping)

Revision ID: d1e2f3a4b5c6
Revises: c2d3e4f5a6b7
Create Date: 2026-07-30

Головная компания отдела (task_org_structure ч.1) — ярлык для дерева
оргструктуры «Компания → Отдел → Сотрудники». На расчёт ЗП не влияет:
часы и распределение по юрлицам остаются мультикомпанийными.

Backfill: если у отдела все активные сотрудники с заданной основной компанией
сходятся на одной — она и проставляется. Спорные случаи остаются пустыми,
головную компанию задаст admin вручную в дереве.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column("head_company_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_departments_head_company_id",
        "departments",
        "companies",
        ["head_company_id"],
        ["id"],
    )

    # Однозначный случай: у всех сотрудников отдела одна и та же основная компания.
    op.execute(
        """
        UPDATE departments d
        SET head_company_id = sub.company_id
        FROM (
            SELECT department_id,
                   MIN(default_company_id) AS company_id,
                   COUNT(DISTINCT default_company_id) AS variants
            FROM employees
            WHERE department_id IS NOT NULL
              AND default_company_id IS NOT NULL
              AND is_active = true
            GROUP BY department_id
        ) AS sub
        WHERE d.id = sub.department_id AND sub.variants = 1
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_departments_head_company_id", "departments", type_="foreignkey")
    op.drop_column("departments", "head_company_id")
