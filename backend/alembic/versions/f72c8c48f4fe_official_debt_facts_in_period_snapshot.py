"""official debt facts in period snapshot

Факт долга по официальной выплате вахты в снимке закрытого периода
(task_official_payout_debt): {position_id: {"1": долг после 1-й половины,
"2": после 2-й}}. Закрытая половина не пересчитывается — её долг берётся
отсюда, как удержание займа берётся из `loan_facts`.

Колонка nullable и без значения по умолчанию: у снимков, снятых до этой
задачи, фактов долга нет — такие половины считаются пропущенными, задним
числом долг в них не появляется (решение заказчика 25.09.2026).

Revision ID: f72c8c48f4fe
Revises: c5d6e7f8a9b0
Create Date: 2026-09-25 14:07:45.304101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f72c8c48f4fe'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.add_column(
        "period_snapshots",
        sa.Column("official_debt_facts", _json_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("period_snapshots", "official_debt_facts")
