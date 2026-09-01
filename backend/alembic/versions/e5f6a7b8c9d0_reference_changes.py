"""reference changes journal (who/when/field/old->new/source)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-01

Журнал изменений справочных данных (task_audit_log). Только новая таблица:
существующий `audit_log` (операции — часы, отсутствия, периоды) не трогается,
данные не переносятся. Журнал начинает вести историю с момента применения
миграции — прошлое в него не попадает и попасть не может.
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Без FK на employees: журнал обязан пережить физическое удаление
        # сотрудника, а не падать на нём и не чиститься каскадом.
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="ui", nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_label", sa.String(length=255), nullable=True),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Журнал растёт быстро; каждый запрос экрана обязан идти по индексу.
    op.create_index("ix_reference_changes_created_at", "reference_changes", ["created_at"])
    op.create_index(
        "ix_reference_changes_entity", "reference_changes", ["entity_type", "entity_id"]
    )
    op.create_index("ix_reference_changes_employee", "reference_changes", ["employee_id"])
    op.create_index("ix_reference_changes_actor", "reference_changes", ["actor_id"])
    op.create_index("ix_reference_changes_operation", "reference_changes", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_reference_changes_operation", table_name="reference_changes")
    op.drop_index("ix_reference_changes_actor", table_name="reference_changes")
    op.drop_index("ix_reference_changes_employee", table_name="reference_changes")
    op.drop_index("ix_reference_changes_entity", table_name="reference_changes")
    op.drop_index("ix_reference_changes_created_at", table_name="reference_changes")
    op.drop_table("reference_changes")
