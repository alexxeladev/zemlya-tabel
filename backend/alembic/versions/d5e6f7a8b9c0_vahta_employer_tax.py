"""vahta: employer tax rate setting

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-16

Настройки вахты (task_vahta_taxes): ставка налоговой нагрузки на официальную
часть выплаты. Налог добавляется к базе разнесения затрат по юрлицам.

Таблица из одной строки; миграция заводит её со ставкой по умолчанию 40 %.
Ставка хранится ПРОЦЕНТАМИ (40.00), а не долей.

Следствие для данных: у строк вахты с заполненной официальной выплатой после
применения миграции разнесение по юрлицам вырастет на сумму налога — это и есть
задача. Строки без официальной выплаты и все прочие подразделения не меняются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table(
        "guard_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employer_tax_percent", sa.Numeric(5, 2), nullable=False,
            server_default="40",
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.bulk_insert(table, [{"id": 1, "employer_tax_percent": 40}])


def downgrade() -> None:
    op.drop_table("guard_settings")
