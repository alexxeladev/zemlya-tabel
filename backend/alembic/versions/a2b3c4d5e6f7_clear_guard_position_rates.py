"""Очистить ставку и оклад у рабочих мест охраны

Цена смены охранника живёт на объекте, посте или экипаже и в строке табеля
вахты (task_guard_form_rate_official). Поле на рабочем месте в расчёте не
участвовало, показывало 2 500 при фактических 4 000 в строке и у четверти
позиций было пустым. Мёртвые значения убираем: после этой миграции у охранной
позиции нет ни ставки, ни оклада, ни ставки за час.

Тип оплаты (`pay_type`) НЕ трогаем — он следует из должности охраны и нужен
расчёту вахты и переводу в обычный отдел.

Обратная миграция ставки не восстанавливает: восстанавливать нечего, значения
были не источником правды. Это записано осознанно, а не забыто.

Revision ID: a2b3c4d5e6f7
Revises: c7d8e9f0a1b2
"""
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE employee_positions
           SET rate = NULL, shift_rate = NULL, hour_rate = NULL
         WHERE department_id IN (
                   SELECT id FROM departments WHERE is_guard_department
               )
           AND (rate IS NOT NULL OR shift_rate IS NOT NULL OR hour_rate IS NOT NULL)
        """
    )


def downgrade() -> None:
    """Ставки охранных позиций не восстанавливаются — их источником правды
    были посты и строки табеля, а не сама позиция."""
