"""override percent precision 6,3 -> 9,6 (task_move_department)

Перенос отдела в другую компанию замораживает распределение ЗАКРЫТЫХ месяцев,
записывая фактический расклад месячным override-ом. Проценты при этом
вычисляются из уже посчитанных сумм, и сумма обязана пересчитаться из них
БИТ В БИТ — иначе «заморозка» сама сдвинет историю.

Замер на 40 000 случайных раскладов: при шаге 0.01 расходится 38 289 случаев
(до 140 ₽), при 0.001 — 30 778 (до 14 ₽), при 0.000001 — ни одного. Поэтому
шесть знаков после запятой.

Расширение точности, значения не меняются: старые проценты (3 знака) остаются
как есть, ручной ввод в ведомости по-прежнему двухзначный.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""
import sqlalchemy as sa

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("company_share_overrides") as batch:
        batch.alter_column(
            "percent",
            existing_type=sa.Numeric(6, 3),
            type_=sa.Numeric(9, 6),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Сужение округляет замороженные проценты до трёх знаков — расклад закрытых
    # месяцев после этого может разъехаться на считанные рубли.
    with op.batch_alter_table("company_share_overrides") as batch:
        batch.alter_column(
            "percent",
            existing_type=sa.Numeric(9, 6),
            type_=sa.Numeric(6, 3),
            existing_nullable=False,
        )
