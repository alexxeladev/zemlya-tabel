"""Вычистка денежных полей из ответов API (task_timekeeper_role).

Табельщик ведёт табель своих отделов, но зарплату видеть не должен. Отказать
эндпойнтам расчёта (`/payroll`, `/statement`, премии, займ) мало: оклад, ставки,
коэффициенты и займ лежат в карточке сотрудника и его позициях, а те приходят
вместе с самим табелем. Поэтому финансы снимаются с уже собранных схем ответа —
на УРОВНЕ API, а не в UI: прямой запрос табельщика тоже вернёт `null`.

Поля перечислены явно (а не «всё, что Decimal»): новое денежное поле должно
осознанно попадать в список, иначе оно молча утечёт. Неденежное — должность,
отдел, график, тип оплаты, признак ночных смен — остаётся: без графика и типа
оплаты табель не построить, а «сколько это стоит» из них не следует.
"""
from __future__ import annotations

from decimal import Decimal

from app.schemas.employee import EmployeeRead
from app.schemas.payroll import PayrollSummaryRead
from app.schemas.position import EmployeePositionRead

_ZERO = Decimal("0")

# Денежные поля позиции: база оплаты и коэффициенты надбавок. Ставки ночной
# смены среди них нет — на позиции её больше не хранят, она вычисляется из
# фонда отдела и гасится ниже, в строках расчёта (task_night_shifts_rework).
FINANCIAL_POSITION_FIELDS = (
    "rate",
    "shift_rate",
    "hour_rate",
    "weekend_coefficient",
    "weekend_fixed_rate",
    "holiday_coefficient",
    "holiday_fixed_rate",
    "overtime_coefficient",
)

# То же в «плоской» карточке сотрудника (это compat-вид основной позиции) плюс займ.
FINANCIAL_EMPLOYEE_FIELDS = FINANCIAL_POSITION_FIELDS + (
    "loan_amount",
    "loan_term_months",
    "loan_start_date",
)


def _blank(model, fields: tuple[str, ...]):
    """Копия схемы с обнулёнными полями. Копия, а не правка на месте: тот же
    объект может уйти в другой ответ, а модели Pydantic здесь строятся из ORM.

    Поля, которых у схемы нет, пропускаем: `model_copy` их не валидирует и молча
    завёл бы лишний атрибут вместо того, чтобы что-то скрыть.
    """
    update = {f: None for f in fields if f in type(model).model_fields}
    return model.model_copy(update=update)


def mask_position(position: EmployeePositionRead) -> EmployeePositionRead:
    return _blank(position, FINANCIAL_POSITION_FIELDS)


def mask_employee(employee: EmployeeRead) -> EmployeeRead:
    masked = _blank(employee, FINANCIAL_EMPLOYEE_FIELDS)
    masked.positions = [mask_position(p) for p in employee.positions]
    return masked


def mask_employees(employees: list[EmployeeRead]) -> list[EmployeeRead]:
    return [mask_employee(e) for e in employees]


def mask_positions_by_employee(
    positions_by_employee: dict[int, list[EmployeePositionRead]],
) -> dict[int, list[EmployeePositionRead]]:
    return {
        emp_id: [mask_position(p) for p in positions]
        for emp_id, positions in positions_by_employee.items()
    }


# ── Расчёт: часы оставить, деньги убрать ──────────────────────────────────────
#
# Табельщик должен видеть ВСЕ часы — норму, переработку, часы вне графика и
# праздничные, дни отпуска и больничного, остаток лимита Б, — иначе он не поймёт,
# правильно ли заполнил табель. Поэтому расчёт для него считается, но денежные
# поля из него вычищаются.
#
# Суммы **обнуляются**, а не превращаются в null: в схеме они обязательные
# `Decimal`, и None не прошёл бы валидацию ответа (500 вместо табеля). Ставки и
# коэффициенты в схеме опциональны — их гасим в None. Реальных сумм в ответе не
# остаётся ни там, ни там.

# Суммы строки расчёта (обязательные Decimal → 0)
_PAYROLL_AMOUNT_FIELDS = (
    "base_amount",
    "overtime_amount",
    "off_schedule_amount",
    "holiday_amount",
    "total_amount",
    # Ночные: сумма надбавки — деньги, а само ЧИСЛО смен (`night_shifts`)
    # остаётся: факт выхода в ночь табельщик отмечает сам.
    "night_amount",
    "vacation_amount",
    "sick_amount",
    "premium_amount",
    "kpi_amount",
    "advance_deduction",
    "loan_deduction",
    "loan_remaining",
    "loan_planned_deduction",
    "total_deductions",
    "net_payout",
    "net_payout_exact",
    "rounding_tail",
)

# Ставки и коэффициенты строки расчёта (Optional → None)
_PAYROLL_RATE_FIELDS = (
    "rate",
    "hourly_rate",
    "shift_rate",
    "hour_rate",
    "weekend_coefficient",
    "weekend_fixed_rate",
    "holiday_coefficient",
    "holiday_fixed_rate",
    "night_rate",
)

# Суммы в разрезе по юрлицам; часы (`hours`, `*_hours`, `percent`) остаются
_BREAKDOWN_AMOUNT_FIELDS = (
    "base_amount",
    "overtime_amount",
    "off_schedule_amount",
    "holiday_amount",
    "total",
)

# Итоги месяца; `total_hours` и `total_employees` остаются
_SUMMARY_AMOUNT_FIELDS = (
    "total_base_amount",
    "total_overtime_amount",
    "total_off_schedule_amount",
    "total_holiday_amount",
    "total_vacation_amount",
    "total_sick_amount",
    "total_night_amount",
    "grand_total",
    "total_premium",
    "total_kpi",
    "total_deductions",
    "total_net_payout",
    "total_net_payout_exact",
    "total_rounding_tail",
)


def mask_payroll_summary(summary: PayrollSummaryRead) -> PayrollSummaryRead:
    """Расчёт без денег: часы, дни, норма, лимит больничного — на месте, суммы и
    ставки — нули/None."""
    rows = []
    for row in summary.employees:
        update: dict[str, object] = {f: _ZERO for f in _PAYROLL_AMOUNT_FIELDS}
        update.update({f: None for f in _PAYROLL_RATE_FIELDS})
        update["breakdown_by_company"] = [
            b.model_copy(update={f: _ZERO for f in _BREAKDOWN_AMOUNT_FIELDS})
            for b in row.breakdown_by_company
        ]
        rows.append(row.model_copy(update=update))

    totals: dict[str, object] = {f: _ZERO for f in _SUMMARY_AMOUNT_FIELDS}
    totals["employees"] = rows
    return summary.model_copy(update=totals)
