"""employee positions (multi-job): move pay/schedule/dept/company to position

Совместительство, task_positions ч.A. Оклад/ставка, график, отдел, компания,
коэффициенты и тип оплаты переезжают с сотрудника на ПОЗИЦИЮ (рабочее место).

Каждому существующему сотруднику создаётся РОВНО ОДНА основная позиция из его
текущих данных, после чего к ней привязываются его часы, премии/KPI/аванс, займ
и проценты распределения. Расчёт после миграции обязан давать те же суммы, что
и до неё: значения переносятся один в один, ничего не пересчитывается.

Revision ID: f1a2b3c4d5e6
Revises: e2f3a4b5c6d7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


# Поля, переезжающие с employees на employee_positions. Слева — колонка позиции,
# справа — колонка сотрудника (совпадают везде, кроме company_id).
_MOVED: list[tuple[str, str]] = [
    ("department_id", "department_id"),
    ("schedule_id", "schedule_id"),
    ("company_id", "default_company_id"),
    ("pay_type", "pay_type"),
    ("rate", "rate"),
    ("shift_rate", "shift_rate"),
    ("weekend_pay_type", "weekend_pay_type"),
    ("weekend_coefficient", "weekend_coefficient"),
    ("weekend_fixed_rate", "weekend_fixed_rate"),
    ("holiday_pay_type", "holiday_pay_type"),
    ("holiday_coefficient", "holiday_coefficient"),
    ("holiday_fixed_rate", "holiday_fixed_rate"),
    ("overtime_coefficient", "overtime_coefficient"),
]

_DROPPED_EMPLOYEE_COLUMNS = [emp_col for _, emp_col in _MOVED]


def upgrade() -> None:
    op.create_table(
        "employee_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("pay_type", sa.String(length=20), server_default="salary", nullable=False),
        sa.Column("rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("shift_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("hour_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("weekend_pay_type", sa.String(length=20), server_default="coefficient", nullable=False),
        sa.Column("weekend_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True),
        sa.Column("weekend_fixed_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("holiday_pay_type", sa.String(length=20), server_default="coefficient", nullable=False),
        sa.Column("holiday_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True),
        sa.Column("holiday_fixed_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("overtime_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True),
        sa.Column("has_night_shifts", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("night_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_employee_positions_employee_id", "employee_positions", ["employee_id"])
    op.create_index("ix_employee_positions_department_id", "employee_positions", ["department_id"])
    op.create_index("ix_position_employee_primary", "employee_positions", ["employee_id", "is_primary"])

    # ── Каждому сотруднику — одна основная позиция из его текущих данных ───────
    pos_cols = ", ".join(pos_col for pos_col, _ in _MOVED)
    emp_cols = ", ".join(emp_col for _, emp_col in _MOVED)
    op.execute(
        f"""
        INSERT INTO employee_positions (employee_id, title, is_primary, is_active, {pos_cols})
        SELECT id, position, true, true, {emp_cols}
        FROM employees
        """
    )

    # ── Привязка существующих данных к основной позиции ────────────────────────
    # position_id везде nullable: NULL читается как «основная» (см. модели), но
    # проставляем сразу, чтобы миграция не оставляла висящих ссылок.
    for table in (
        "timesheet_entries",
        "employee_adjustments",
        "employee_company_shares",
        "company_share_overrides",
    ):
        op.add_column(table, sa.Column("position_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_position", table, "employee_positions", ["position_id"], ["id"]
        )
        op.create_index(f"ix_{table}_position_id", table, ["position_id"])
        op.execute(
            f"""
            UPDATE {table} SET position_id = (
                SELECT p.id FROM employee_positions p
                WHERE p.employee_id = {table}.employee_id AND p.is_primary
            )
            """
        )

    # Займ — на человеке, но удерживается с конкретной позиции.
    op.add_column("employees", sa.Column("loan_position_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_employees_loan_position", "employees", "employee_positions",
        ["loan_position_id"], ["id"],
    )
    op.execute(
        """
        UPDATE employees SET loan_position_id = (
            SELECT p.id FROM employee_positions p
            WHERE p.employee_id = employees.id AND p.is_primary
        )
        WHERE loan_amount IS NOT NULL
        """
    )

    # ── Уникальность ячейки табеля теперь учитывает позицию ───────────────────
    with op.batch_alter_table("timesheet_entries") as batch:
        batch.drop_constraint("uq_timesheet_employee_date_company", type_="unique")
        batch.create_unique_constraint(
            "uq_timesheet_employee_date_company",
            ["employee_id", "position_id", "work_date", "company_id"],
        )
    with op.batch_alter_table("employee_company_shares") as batch:
        batch.drop_constraint("uq_emp_company_share", type_="unique")
        batch.create_unique_constraint(
            "uq_emp_company_share", ["employee_id", "position_id", "company_id"]
        )
    with op.batch_alter_table("company_share_overrides") as batch:
        batch.drop_constraint("uq_company_share_override_period", type_="unique")
        batch.create_unique_constraint(
            "uq_company_share_override_period",
            ["employee_id", "position_id", "company_id", "year", "month"],
        )

    # ── Колонки, уехавшие на позицию, с сотрудника снимаются ──────────────────
    # Источник правды один: два места хранения оклада разъехались бы, и зарплата
    # зависела бы от того, какое из них прочитали.
    with op.batch_alter_table("employees") as batch:
        for column in _DROPPED_EMPLOYEE_COLUMNS:
            batch.drop_column(column)


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("schedule_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("default_company_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pay_type", sa.String(length=20), server_default="salary", nullable=False))
        batch.add_column(sa.Column("rate", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("shift_rate", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("weekend_pay_type", sa.String(length=20), server_default="coefficient", nullable=False))
        batch.add_column(sa.Column("weekend_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True))
        batch.add_column(sa.Column("weekend_fixed_rate", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("holiday_pay_type", sa.String(length=20), server_default="coefficient", nullable=False))
        batch.add_column(sa.Column("holiday_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True))
        batch.add_column(sa.Column("holiday_fixed_rate", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("overtime_coefficient", sa.Numeric(4, 2), server_default="1.5", nullable=True))

    # Возвращаем данные ОСНОВНОЙ позиции; совместительство при откате теряется.
    set_clause = ", ".join(
        f"{emp_col} = (SELECT p.{pos_col} FROM employee_positions p "
        f"WHERE p.employee_id = employees.id AND p.is_primary)"
        for pos_col, emp_col in _MOVED
    )
    op.execute(f"UPDATE employees SET {set_clause}")

    with op.batch_alter_table("company_share_overrides") as batch:
        batch.drop_constraint("uq_company_share_override_period", type_="unique")
        batch.create_unique_constraint(
            "uq_company_share_override_period",
            ["employee_id", "company_id", "year", "month"],
        )
    with op.batch_alter_table("employee_company_shares") as batch:
        batch.drop_constraint("uq_emp_company_share", type_="unique")
        batch.create_unique_constraint("uq_emp_company_share", ["employee_id", "company_id"])
    with op.batch_alter_table("timesheet_entries") as batch:
        batch.drop_constraint("uq_timesheet_employee_date_company", type_="unique")
        batch.create_unique_constraint(
            "uq_timesheet_employee_date_company", ["employee_id", "work_date", "company_id"]
        )

    op.drop_constraint("fk_employees_loan_position", "employees", type_="foreignkey")
    op.drop_column("employees", "loan_position_id")

    for table in (
        "company_share_overrides",
        "employee_company_shares",
        "employee_adjustments",
        "timesheet_entries",
    ):
        op.drop_index(f"ix_{table}_position_id", table_name=table)
        op.drop_constraint(f"fk_{table}_position", table, type_="foreignkey")
        op.drop_column(table, "position_id")

    op.drop_index("ix_position_employee_primary", table_name="employee_positions")
    op.drop_index("ix_employee_positions_department_id", table_name="employee_positions")
    op.drop_index("ix_employee_positions_employee_id", table_name="employee_positions")
    op.drop_table("employee_positions")
