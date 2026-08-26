"""department applications distribution (task_hr_applications)

Отдел с флагом `uses_applications_distribution` (HR) распределяет зарплату
сотрудников по числу заявок на подбор, отработанных для каждого юрлица за месяц,
вместо обычного каскада процентов. Заявки помесячные — отсюда (отдел, компания,
год, месяц) в ключе.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
import sqlalchemy as sa

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Флаг отдела. server_default false — существующие отделы остаются на
    # каскаде, ничего не меняется без явного включения.
    op.add_column(
        "departments",
        sa.Column(
            "uses_applications_distribution",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "department_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id", "company_id", "year", "month",
            name="uq_department_application_period",
        ),
    )
    op.create_index(
        op.f("ix_department_applications_department_id"),
        "department_applications",
        ["department_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_department_applications_department_id"),
        table_name="department_applications",
    )
    op.drop_table("department_applications")
    op.drop_column("departments", "uses_applications_distribution")
