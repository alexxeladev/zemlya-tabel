"""
Врезка вахты в ОБЩУЮ ведомость (task_vahta).

Лист «Охрана» в образце заказчика — это лист той же ведомости, что и у прочих
подразделений, с теми же колонками. Поэтому строки вахты идут не отдельным
миром, а обычными строками `/statement` и `/payroll` — просто посчитанными
модулем вахты.

**Позиция, у которой в этом месяце есть назначение вахты, считается ВАХТОЙ, а
не обычным расчётом.** Иначе её посчитали бы дважды или (что вероятнее) по
нулям: часов в табеле у охранника нет, вся его работа — отметки смен.

Три отличия от общего расчёта, которые эта врезка обязана сохранить:

* база распределения — затраты: «Итого начислено» ВКЛЮЧАЯ премию (75 000 +
  230 = 75 230) ПЛЮС налог на официальную часть выплаты (task_vahta_taxes:
  оф. выплата 25 230 × 40 % = 10 092 → база 85 322). Поэтому у строки вахты
  сумма по юрлицам больше «Итого начислено» ровно на налог;
* «к выплате» округляется ВВЕРХ до 500 ₽ по каждой половине (в основной
  системе — к ближайшей тысяче), доли по юрлицам не округляются вовсе;
* проценты берутся от МЕСТА РАБОТЫ — у поста от его объекта, у выездного
  экипажа ГБР от самого экипажа, — а каскад (месяц → карточка → отдел → часы)
  к этим строкам не применяется вовсе.

Норма и факт часов — круглосуточные: норма 744 часа при 31 дне, факт = смены ×
24 (15 смен → 360). Так в ведомости заказчика.

**Что в строку вахты НЕ входит.** Премии/KPI/аванс основной системы и займ к
строкам вахты не применяются: у вахты своя премия, свой штраф и своя
официальная выплата, и складывать два набора начислений задача не описывает.
Если охраннику понадобится займ, его надо будет заводить отдельной задачей.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session, selectinload

from app.models.guard_assignments import GuardAssignment
from app.models.guard_posts import GuardCrew, GuardPost, GuardSite
from app.models.positions import PAY_TYPE_PER_SHIFT, EmployeePosition
from app.schemas.payroll import EmployeePayrollRead
from app.services.guard_duty import employer_tax_percent, shares_map
from app.services.guard_month import calculate_assignment
from app.services.guard_payroll import (
    HOURS_PER_SHIFT,
    GuardHalfResult,
    GuardRowResult,
    norm_days_for_month,
    norm_hours_for_month,
)

_ZERO = Decimal("0")

#: Откуда взято распределение строки вахты — новое значение
#: `StatementRow.distribution_source` рядом с month/employee/department/hours.
SOURCE_GUARD_POST = "guard_post"


class GuardStatementRow:
    """Готовая к ведомости строка вахты: расчёт + проценты её поста."""

    __slots__ = ("assignment", "result", "shares")

    def __init__(
        self,
        assignment: GuardAssignment,
        result: GuardRowResult,
        shares: dict[int, Decimal],
    ) -> None:
        self.assignment = assignment
        self.result = result
        self.shares = shares

    @property
    def post_name(self) -> str:
        return self.assignment.place_name

    @property
    def kind_label(self) -> str:
        return self.assignment.kind_label


def load_guard_rows(
    db: Session, position_ids: list[int], year: int, month: int
) -> dict[int, GuardStatementRow]:
    """{position_id: строка вахты} за месяц.

    У одного рабочего места может быть НЕСКОЛЬКО назначений за месяц — так
    выглядит замена на посту (дни поделены между прежним и сменщиком). Для
    ведомости они складываются в одну строку позиции: платят человеку один раз,
    а строк в ведомости столько, сколько у него рабочих мест.
    """
    ids = [p for p in position_ids if p is not None]
    if not ids:
        return {}

    assignments = (
        db.query(GuardAssignment)
        .options(
            selectinload(GuardAssignment.shifts),
            selectinload(GuardAssignment.post)
            .selectinload(GuardPost.site)
            .selectinload(GuardSite.shares),
            selectinload(GuardAssignment.crew).selectinload(GuardCrew.shares),
        )
        .filter(
            GuardAssignment.year == year,
            GuardAssignment.month == month,
            GuardAssignment.position_id.in_(ids),
        )
        .order_by(GuardAssignment.id)
        .all()
    )

    tax_percent = employer_tax_percent(db) if assignments else None
    rows: dict[int, GuardStatementRow] = {}
    for assignment in assignments:
        result = calculate_assignment(assignment, tax_percent=tax_percent)
        existing = rows.get(assignment.position_id)
        if existing is None:
            rows[assignment.position_id] = GuardStatementRow(
                assignment, result, shares_map(assignment.place)
            )
        else:
            existing.result = _merge(existing.result, result)
    return rows


def _merge(left: GuardRowResult, right: GuardRowResult) -> GuardRowResult:
    """Сложить два назначения одного рабочего места (замена внутри месяца)."""
    halves = {}
    for half in set(left.halves) | set(right.halves):
        a = left.halves.get(half)
        b = right.halves.get(half)
        if a is None or b is None:
            halves[half] = a or b
            continue
        halves[half] = GuardHalfResult(
            half=half,
            shifts=a.shifts + b.shifts,
            salary=a.salary + b.salary,
            premium=a.premium + b.premium,
            penalty=a.penalty + b.penalty,
            official_payout=a.official_payout + b.official_payout,
            tax=a.tax + b.tax,
        )
    return GuardRowResult(halves=halves)


def guard_payroll_read(
    employee, position: EmployeePosition, row: GuardStatementRow, year: int, month: int
) -> EmployeePayrollRead:
    """Строка расчёта для ведомости и табеля, посчитанная вахтой.

    Раскладка по колонкам ведомости повторяет лист «Охрана» образца:
    «Начислено, оклад» — зарплата за смены, «Премия базовая» — премия вахты,
    «Итого начислено» — зарплата + премия − штраф, «выплачено аванс\\удержано» —
    официальная (банковская) выплата, «Сумма к выплате» — остаток из кассы.
    """
    result = row.result
    return EmployeePayrollRead(
        employee_id=employee.id,
        employee_name=employee.full_name,
        position_id=position.id,
        position_title=row.kind_label,
        is_primary_position=bool(position.is_primary),
        rate=Decimal(str(row.assignment.rate)),
        schedule_name=None,
        pay_type=PAY_TYPE_PER_SHIFT,
        shift_rate=Decimal(str(row.assignment.rate)),
        hour_rate=None,
        worked_shifts=result.shifts,
        norm_shifts=norm_days_for_month(year, month),
        base_shifts=result.shifts,
        # Круглосуточный пост: норма — ВСЕ часы месяца, факт — смены × 24.
        total_hours=Decimal(result.fact_hours),
        norm_hours=Decimal(norm_hours_for_month(year, month)),
        delta_hours=Decimal(result.fact_hours - norm_hours_for_month(year, month)),
        overtime_hours=_ZERO,
        off_schedule_hours=_ZERO,
        holiday_hours=_ZERO,
        norm_days=norm_days_for_month(year, month),
        fact_days=result.shifts,
        hourly_rate=None,
        base_amount=result.salary,
        overtime_amount=_ZERO,
        off_schedule_amount=_ZERO,
        holiday_amount=_ZERO,
        total_amount=result.salary,
        night_shifts=0,
        night_rate=None,
        night_amount=_ZERO,
        premium_amount=result.premium,
        kpi_amount=_ZERO,
        # Штраф вахты уменьшает «Итого начислено» (и, значит, базу распределения):
        # так сформулировано в ТЗ — «зарплата плюс премия минус штраф».
        guard_penalty_amount=result.penalty,
        # Налог на официальную часть: в «Итого начислено» не входит, но
        # увеличивает базу распределения по юрлицам (task_vahta_taxes).
        guard_tax_amount=result.tax,
        advance_deduction=result.official_payout,
        loan_deduction=_ZERO,
        loan_remaining=_ZERO,
        loan_planned_deduction=_ZERO,
        loan_is_manual=False,
        total_deductions=result.official_payout,
        # «К выплате» вверх до 500 ₽ по каждой половине; хвост ≤ 0 (доплата).
        net_payout=result.net_payout,
        net_payout_exact=result.net_payout_exact,
        rounding_tail=result.rounding_tail,
        breakdown_by_company=[],
        is_calculable=True,
        reason_if_not_calculable=None,
        is_guard_row=True,
    )


def guard_hours_per_shift() -> int:
    """Смена вахты равна суткам — 24 часа."""
    return HOURS_PER_SHIFT
