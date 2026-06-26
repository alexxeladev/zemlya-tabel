"""company cost distribution shares (default + monthly override)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-06-26

Распределение затрат на сотрудника между юрлицами в процентах (задача 3.11b п.1):
- employee_company_shares — проценты по умолчанию из карточки сотрудника;
- company_share_overrides — помесячное переопределение (гибрид как у займа).
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_company_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("employee_id", "company_id", name="uq_emp_company_share"),
    )
    op.create_index(
        "ix_employee_company_shares_employee_id",
        "employee_company_shares",
        ["employee_id"],
    )

    op.create_table(
        "company_share_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Numeric(6, 3), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "employee_id", "company_id", "year", "month",
            name="uq_company_share_override_period",
        ),
    )
    op.create_index(
        "ix_company_share_overrides_employee_id",
        "company_share_overrides",
        ["employee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_share_overrides_employee_id", "company_share_overrides")
    op.drop_table("company_share_overrides")
    op.drop_index("ix_employee_company_shares_employee_id", "employee_company_shares")
    op.drop_table("employee_company_shares")
