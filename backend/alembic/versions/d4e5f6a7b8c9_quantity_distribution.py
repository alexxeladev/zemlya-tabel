"""generalize applications distribution to quantity metric (ARM for IT)

Обобщение механизма «распределение по заявкам на подбор» (task_hr_applications)
до распределения по ЛЮБОМУ количественному показателю отдела
(task_it_arm_distribution): у HR это заявки, у ИТ — число АРМ.

Данные не теряются и не меняются: таблица и колонки переименовываются, а
отделам, у которых распределение по заявкам уже включено, проставляются подписи
показателя («Заявки», «В работе», «Закрытые») — то есть после миграции HR
выглядит и считается ровно как раньше.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("department_applications", "department_quantities")
    op.alter_column("department_quantities", "in_progress", new_column_name="part1")
    op.alter_column("department_quantities", "closed", new_column_name="part2")
    op.execute(
        "ALTER TABLE department_quantities "
        "RENAME CONSTRAINT uq_department_application_period "
        "TO uq_department_quantity_period"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_department_applications_department_id "
        "RENAME TO ix_department_quantities_department_id"
    )

    op.alter_column(
        "departments",
        "uses_applications_distribution",
        new_column_name="uses_quantity_distribution",
    )
    op.add_column(
        "departments", sa.Column("quantity_metric_name", sa.String(64), nullable=True)
    )
    op.add_column(
        "departments", sa.Column("quantity_part1_name", sa.String(64), nullable=True)
    )
    op.add_column(
        "departments", sa.Column("quantity_part2_name", sa.String(64), nullable=True)
    )
    # Отделы, уже распределяющиеся по заявкам, сохраняют прежние подписи —
    # иначе после деплоя HR-блок молча переименовался бы в «Количество».
    op.execute(
        "UPDATE departments SET quantity_metric_name = 'Заявки', "
        "quantity_part1_name = 'В работе', quantity_part2_name = 'Закрытые' "
        "WHERE uses_quantity_distribution = true"
    )


def downgrade() -> None:
    op.drop_column("departments", "quantity_part2_name")
    op.drop_column("departments", "quantity_part1_name")
    op.drop_column("departments", "quantity_metric_name")
    op.alter_column(
        "departments",
        "uses_quantity_distribution",
        new_column_name="uses_applications_distribution",
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_department_quantities_department_id "
        "RENAME TO ix_department_applications_department_id"
    )
    op.execute(
        "ALTER TABLE department_quantities "
        "RENAME CONSTRAINT uq_department_quantity_period "
        "TO uq_department_application_period"
    )
    op.alter_column("department_quantities", "part2", new_column_name="closed")
    op.alter_column("department_quantities", "part1", new_column_name="in_progress")
    op.rename_table("department_quantities", "department_applications")
