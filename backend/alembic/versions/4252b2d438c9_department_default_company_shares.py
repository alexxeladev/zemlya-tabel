"""department default company shares

Revision ID: 4252b2d438c9
Revises: e8f9a0b1c2d3
Create Date: 2026-07-27

Распределение затрат по юрлицам по умолчанию на уровне ОТДЕЛА
(task_distribution_v2 ч.3). Наследуется сотрудниками отдела, у которых нет своего
распределения. Каскад: месячный % > карточка сотрудника > отдел > авто по часам.

Автогенерация дополнительно предлагала alter_column created_at/updated_at
(TIMESTAMP → String, артефакт аннотации Mapped[str] в моделях) и удаление
partial unique index-ов timesheet_periods (alembic их не видит) — не переносим,
это ломало бы существующую схему.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "4252b2d438c9"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "department_company_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("department_id", "company_id", name="uq_dept_company_share"),
    )
    op.create_index(
        "ix_department_company_shares_department_id",
        "department_company_shares",
        ["department_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_department_company_shares_department_id",
        table_name="department_company_shares",
    )
    op.drop_table("department_company_shares")
