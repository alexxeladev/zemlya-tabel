"""Официальное трудоустройство — на рабочем месте, выплата вычисляется

task_guard_form_rate_official. Признак «официально устроен» и официальная
зарплата НА РУКИ переезжают со строки табеля вахты на РАБОЧЕЕ МЕСТО, а суммы
официальных выплат по половинам перестают храниться вовсе: они вычисляются из
зарплаты (`guard_duty.official_month_payouts`).

**Как выводится зарплата из прежних данных.** Берётся САМЫЙ РАННИЙ месяц, где у
рабочего места есть официальные выплаты, и зарплата = `h1 + h2` этого месяца
(через банк уходит вся месячная сумма, в одну половину или в две). Месяц
пропускается, если:

* выплат в нём нет вовсе (пустой) — тогда смотрим следующий;
* обе половины ненулевые и РАЗНЫЕ — это догоняющая выплата (12 615 + 25 230 =
  37 845), и взять её за месячную зарплату значило бы завысить её в полтора
  раза.

Ни один месяц не подошёл — признак остаётся включённым, а зарплата пустой: её
заполнят руками. Выдумывать сумму нельзя, от неё считаются выплата и налог.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

_ZERO = Decimal("0")


def _month_totals(bind) -> dict[int, list[tuple[int, int, Decimal, Decimal]]]:
    """{position_id: [(год, месяц, Σh1, Σh2), …]} по возрастанию месяцев.

    Строк у рабочего места в месяце может быть несколько (замена на посту) —
    половины складываются, платят человеку один раз.
    """
    rows = bind.execute(
        sa.text(
            """
            SELECT position_id, year, month,
                   SUM(official_payout_h1) AS h1,
                   SUM(official_payout_h2) AS h2
              FROM guard_assignments
             WHERE position_id IS NOT NULL
               AND (is_official OR official_payout_h1 > 0 OR official_payout_h2 > 0)
             GROUP BY position_id, year, month
             ORDER BY position_id, year, month
            """
        )
    ).fetchall()
    out: dict[int, list[tuple[int, int, Decimal, Decimal]]] = {}
    for position_id, year, month, h1, h2 in rows:
        out.setdefault(position_id, []).append(
            (year, month, Decimal(str(h1 or 0)), Decimal(str(h2 or 0)))
        )
    return out


def _derive_salary(months: list[tuple[int, int, Decimal, Decimal]]) -> Decimal | None:
    for _year, _month, h1, h2 in months:
        total = h1 + h2
        if total <= _ZERO:
            continue
        if h1 > _ZERO and h2 > _ZERO and h1 != h2:
            continue  # догоняющая выплата — за месячную зарплату не годится
        return total
    return None


def upgrade() -> None:
    op.add_column(
        "employee_positions",
        sa.Column(
            "is_official", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "employee_positions",
        sa.Column("official_salary", sa.Numeric(12, 2), nullable=True),
    )

    bind = op.get_bind()
    official_positions = [
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT DISTINCT position_id
                  FROM guard_assignments
                 WHERE position_id IS NOT NULL
                   AND (is_official OR official_payout_h1 > 0 OR official_payout_h2 > 0)
                """
            )
        )
    ]
    totals = _month_totals(bind)
    for position_id in official_positions:
        salary = _derive_salary(totals.get(position_id, []))
        bind.execute(
            sa.text(
                "UPDATE employee_positions "
                "   SET is_official = true, official_salary = :salary "
                " WHERE id = :id"
            ),
            {"salary": salary, "id": position_id},
        )

    # Прежние поля строки табеля: флаг ни на что не влиял, суммы вводили руками
    # каждый месяц. Оба источника сняты — второго места для этих данных нет.
    op.drop_column("guard_assignments", "is_official")
    op.drop_column("guard_assignments", "official_payout_h1")
    op.drop_column("guard_assignments", "official_payout_h2")


def downgrade() -> None:
    op.add_column(
        "guard_assignments",
        sa.Column(
            "is_official", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "guard_assignments",
        sa.Column(
            "official_payout_h1", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "guard_assignments",
        sa.Column(
            "official_payout_h2", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
    )
    # Флаг возвращаем строкам официальных рабочих мест; суммы по половинам
    # восстановить неоткуда — они были ручным вводом, а стали производными.
    op.execute(
        """
        UPDATE guard_assignments a
           SET is_official = true
          FROM employee_positions p
         WHERE p.id = a.position_id AND p.is_official
        """
    )
    op.drop_column("employee_positions", "official_salary")
    op.drop_column("employee_positions", "is_official")
