"""email case insensitive unique

Revision ID: c7d8e9f0a1b2
Revises: 1033950c4ced
Create Date: 2026-09-19 08:37:38.766820

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = '1033950c4ced'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Почта без учёта регистра и вход по части до «@» (решение заказчика,
    19.09.2026; services/accounts).

    Сначала ПРОВЕРКА, потом правка: если в базе есть почты, отличающиеся только
    регистром, или две учётки с одинаковой частью до «@», миграция отказывает
    с перечнем — кому какой адрес оставить, решает человек, а не миграция.
    Отказ откатывает транзакцию: база и работающая версия не меняются.
    """
    conn = op.get_bind()
    problems: list[str] = []
    for label, expr in (
        ("почта отличается только регистром", "lower(email)"),
        ("одинаковый логин (часть почты до @)", "lower(split_part(email, '@', 1))"),
    ):
        rows = conn.execute(sa.text(
            f"SELECT {expr} AS k, string_agg(email || ' (id ' || id || ')', ', ' ORDER BY id) "
            f"FROM employees WHERE email IS NOT NULL GROUP BY {expr} HAVING count(*) > 1"
        )).all()
        problems += [f"{label}: {k} — {who}" for k, who in rows]
    if problems:
        raise RuntimeError(
            "Вход по логину требует уникальной части почты до «@». Разведите учётки "
            "(поменяйте почту или снимите доступ) и повторите миграцию:\n  "
            + "\n  ".join(problems)
        )
    conn.execute(sa.text("UPDATE employees SET email = lower(email) WHERE email <> lower(email)"))
    op.create_index(
        "uq_employees_email_lower", "employees", [sa.text("lower(email)")], unique=True,
    )
    # Логин (часть до «@») уникален и в БАЗЕ: проверку приложения
    # (`account_conflict`) две одновременные выдачи доступа прошли бы обе
    # (нашло ревью). split_part — только Postgres; в модели этого индекса нет,
    # тестам на SQLite хватает проверки приложения.
    op.create_index(
        "uq_employees_login_name", "employees",
        [sa.text("lower(split_part(email, '@', 1))")], unique=True,
    )


def downgrade() -> None:
    # Регистр почт до миграции не восстановить — он и не нужен.
    op.drop_index("uq_employees_login_name", table_name="employees")
    op.drop_index("uq_employees_email_lower", table_name="employees")
