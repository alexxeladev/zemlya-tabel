"""Историчность расчёта: версии условий, снимок закрытого периода

task_stage3_historicity.

1. `position_terms` — версии условий труда рабочего места. Каждой позиции
   заводится ОДНА версия «с начала времён» (1900-01-01) из текущих значений её
   полей. Расчёт после миграции берёт ровно те же значения, что и до неё, —
   ни одна сумма ни одного месяца не должна сдвинуться (главная проверка
   этапа, сверка построчно).
2. `employee_company_shares.effective_from` — с какого месяца действует набор
   процентов рабочего места. Существующим наборам — 1900-01-01. Уникальность
   расширяется датой: у позиции может быть несколько наборов.
3. `guard_tax_rates` — версии ставки налога вахты; первая «с начала времён» из
   `guard_settings` (нет строки — 40 %, как и читал расчёт).
4. `period_snapshots` — снимок расчёта закрытого периода. Пустая: снимки для
   уже закрытых периодов снимаются отдельной командой после отчёта заказчику
   (`python -m app.cli backfill-snapshots`), это необратимо и требует
   подтверждения.

Всё SQL-ом одним INSERT … SELECT на таблицу: миграция трогает все позиции, и
построчная вставка из Python удлинила бы окно деплоя.

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
"""
import sqlalchemy as sa

from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None

_TERMS = (
    "pay_type, rate, shift_rate, hour_rate, schedule_id, "
    "weekend_pay_type, weekend_coefficient, weekend_fixed_rate, "
    "holiday_pay_type, holiday_coefficient, holiday_fixed_rate, "
    "overtime_coefficient, is_official, official_salary"
)


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_type = _json_type()

    # ── 1. Версии условий ────────────────────────────────────────────────────
    op.create_table(
        "position_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id", sa.Integer(),
            sa.ForeignKey("employee_positions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("pay_type", sa.String(20), nullable=False),
        sa.Column("rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("shift_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("hour_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id"), nullable=True),
        sa.Column("weekend_pay_type", sa.String(20), nullable=False),
        sa.Column("weekend_coefficient", sa.Numeric(4, 2), nullable=True),
        sa.Column("weekend_fixed_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("holiday_pay_type", sa.String(20), nullable=False),
        sa.Column("holiday_coefficient", sa.Numeric(4, 2), nullable=True),
        sa.Column("holiday_fixed_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("overtime_coefficient", sa.Numeric(4, 2), nullable=True),
        sa.Column("is_official", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("official_salary", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("position_id", "effective_from", name="uq_position_terms_date"),
    )
    op.create_index("ix_position_terms_position_id", "position_terms", ["position_id"])
    op.execute(
        f"INSERT INTO position_terms (position_id, effective_from, {_TERMS}, created_by_name) "
        f"SELECT id, DATE '1900-01-01', {_TERMS}, 'миграция (этап 3)' FROM employee_positions"
    )

    # ── 2. Распределение по юрлицам: с какого месяца ─────────────────────────
    with op.batch_alter_table("employee_company_shares") as batch:
        batch.add_column(
            sa.Column(
                "effective_from", sa.Date(), nullable=False, server_default="1900-01-01"
            )
        )
        batch.drop_constraint("uq_emp_company_share", type_="unique")
        batch.create_unique_constraint(
            "uq_emp_company_share",
            ["employee_id", "position_id", "company_id", "effective_from"],
        )

    # ── 3. Версии ставки налога вахты ────────────────────────────────────────
    op.create_table(
        "guard_tax_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("effective_from", sa.Date(), nullable=False, unique=True),
        sa.Column("employer_tax_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO guard_tax_rates (effective_from, employer_tax_percent, created_by_name) "
        "SELECT DATE '1900-01-01', COALESCE("
        "(SELECT employer_tax_percent FROM guard_settings ORDER BY id LIMIT 1), 40), "
        "'миграция (этап 3)'"
    )

    # ── 4. Снимки закрытых периодов ──────────────────────────────────────────
    op.create_table(
        "period_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "period_id", sa.Integer(),
            sa.ForeignKey("timesheet_periods.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("position_ids", json_type, nullable=False),
        sa.Column("payroll_rows", json_type, nullable=False),
        sa.Column("statement_rows", json_type, nullable=False),
        sa.Column("dashboard_rows", json_type, nullable=False),
        sa.Column("terms_used", json_type, nullable=False),
        sa.Column("loan_facts", json_type, nullable=False),
        sa.Column("guard_views", json_type, nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("period_id", name="uq_period_snapshot_period"),
    )
    op.create_index("ix_period_snapshots_department_id", "period_snapshots", ["department_id"])
    op.create_index("ix_period_snapshots_year", "period_snapshots", ["year"])


def downgrade() -> None:
    op.drop_index("ix_period_snapshots_year", table_name="period_snapshots")
    op.drop_index("ix_period_snapshots_department_id", table_name="period_snapshots")
    op.drop_table("period_snapshots")
    op.drop_table("guard_tax_rates")
    # Наборы процентов с датой позже начала времён — это история, в старой схеме
    # ей места нет: остаётся последний набор каждой позиции (как видит его
    # карточка), прочие удаляются, иначе не встанет прежняя уникальность.
    op.execute(
        "DELETE FROM employee_company_shares s WHERE s.effective_from < ("
        "SELECT max(t.effective_from) FROM employee_company_shares t "
        "WHERE t.employee_id = s.employee_id "
        "AND t.position_id IS NOT DISTINCT FROM s.position_id)"
    )
    with op.batch_alter_table("employee_company_shares") as batch:
        batch.drop_constraint("uq_emp_company_share", type_="unique")
        batch.create_unique_constraint(
            "uq_emp_company_share", ["employee_id", "position_id", "company_id"]
        )
        batch.drop_column("effective_from")
    # Поля позиции — зеркало последней версии, поэтому с удалением истории
    # позиция остаётся с теми условиями, что видны в карточке.
    op.drop_index("ix_position_terms_position_id", table_name="position_terms")
    op.drop_table("position_terms")
