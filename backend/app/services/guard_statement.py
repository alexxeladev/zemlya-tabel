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
from app.services.guard_duty import (
    employer_tax_percent,
    official_by_assignment,
    shares_map,
)
from app.services.guard_month import calculate_assignment
from app.services.guard_payroll import (
    HOURS_PER_SHIFT,
    GuardHalfResult,
    GuardRowResult,
    norm_days_for_month,
    norm_hours_for_month,
)
from app.services.official_debt import DebtSummary
from app.services.payroll import EmployeePayroll

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
    def job_title_name(self) -> str:
        return self.assignment.job_title_name


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

    tax_percent = employer_tax_percent(db, year, month) if assignments else None
    # Официальная выплата — из оф. зарплаты рабочего места, одна на место за
    # месяц (task_guard_form_rate_official). Считаем по всему набору строк: при
    # замене внутри месяца выплата делится между строками, а не удваивается.
    official = official_by_assignment(assignments)
    rows: dict[int, GuardStatementRow] = {}
    for assignment in assignments:
        result = calculate_assignment(
            assignment, tax_percent=tax_percent, official=official.get(assignment.id)
        )
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


def _debt_applies(debt: DebtSummary | None) -> bool:
    """Считать ли «к выплате» по состоянию долга.

    `covered=False` — месяц вне истории долга (раньше первого поста) ИЛИ
    закрытый без снимка: такой период остаётся ровно таким, каким был, иначе мы
    правили бы закрытый месяц (нашло ревью).
    """
    return debt is not None and debt.covered


def guard_payroll_read(
    employee,
    position: EmployeePosition,
    row: GuardStatementRow,
    year: int,
    month: int,
    debt: DebtSummary | None = None,
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
        position_title=row.job_title_name,
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
        # С task_official_payout_debt из неё вычитается ещё и долг прошлых
        # половин, а отрицательный остаток не выплачивается, а переносится
        # дальше — поэтому сумма берётся у состояния долга, если оно посчитано.
        net_payout=debt.payout if _debt_applies(debt) else result.net_payout,
        # Точная сумма и хвост округления считаются от ТОЙ ЖЕ выплаты, что и
        # «к выплате»: иначе строка показывала бы округление от суммы, которую
        # уже не выдают (минус половины теперь уходит в долг, а не вычитается).
        net_payout_exact=(
            debt.payout_exact if _debt_applies(debt) else result.net_payout_exact
        ),
        rounding_tail=(
            debt.rounding_tail if _debt_applies(debt) else result.rounding_tail
        ),
        # Долг показывается и у месяца, который сам не пересчитывается: он
        # пришёл из прошлых половин и поедет дальше.
        official_debt_before=debt.debt_before if debt else _ZERO,
        official_debt_after=debt.debt_after if debt else _ZERO,
        official_debt_repaid=debt.repaid if debt else _ZERO,
        breakdown_by_company=[],
        is_calculable=True,
        reason_if_not_calculable=None,
        is_guard_row=True,
    )


def guard_idle_payroll(employee, position: EmployeePosition) -> EmployeePayroll:
    """Нулевой РАСЧЁТ охранного места, которое в этом месяце НЕ СТОИТ НА ПОСТУ.

    Работа охранника — только отметки смен вахты. Рабочее место без строки
    табеля вахты не заработало ничего, и в общий расчёт его пускать нельзя ни
    при каком графике: посменная оплата посчитала бы «плановые смены графика ×
    ставку», а ставки у охранной позиции больше нет вовсе (см. правила вахты,
    «Цена смены охранника»). До этой правки на деве так начислялось 653 704 ₽
    за июль и 435 111 ₽ за август — деньги из графика и часов, оставшихся от
    демо-данных.

    Обнуляется ТОЛЬКО заработок. Премии/KPI/аванс и удержание займа к строке
    применяет общий путь ведомости — они адресованы рабочему месту и от того,
    стоял ли человек на посту, не зависят: «сами данные запрет не трогает»
    (правило вахты про закрытый общий ввод).

    Строка остаётся РАСЧЁТНОЙ (`is_calculable=True`): ноль здесь — полный и
    верный ответ, а не «карточка не заполнена». В KPI «не вошли в расчёт ФОТ»
    такие места попадать не должны.
    """
    return EmployeePayroll(
        employee_id=employee.id,
        employee_name=employee.full_name,
        position_id=position.id,
        position_title=position.title,
        is_primary_position=bool(position.is_primary),
        rate=None,
        schedule_name=None,
        pay_type=position.pay_type,
        shift_rate=None,
        hour_rate=None,
        worked_shifts=0,
        norm_shifts=0,
        base_shifts=0,
        # Ни плана, ни факта: на посту в этом месяце человека не было.
        total_hours=_ZERO,
        norm_hours=_ZERO,
        delta_hours=_ZERO,
        overtime_hours=_ZERO,
        off_schedule_hours=_ZERO,
        holiday_hours=_ZERO,
        norm_days=0,
        fact_days=0,
        hourly_rate=None,
        base_amount=_ZERO,
        overtime_amount=_ZERO,
        off_schedule_amount=_ZERO,
        holiday_amount=_ZERO,
        total_amount=_ZERO,
        night_shifts=0,
        night_rate=None,
        night_amount=_ZERO,
        vacation_days=0,
        unpaid_days=0,
        sick_days=0,
        absent_days=0,
        vacation_paid_days=0,
        sick_paid_days=0,
        vacation_amount=_ZERO,
        sick_amount=_ZERO,
        sick_limit_days=0,
        sick_days_used_before=0,
        sick_unpaid_days=0,
        sick_limit_remaining=0,
        breakdown_by_company=[],
        is_calculable=True,
        reason_if_not_calculable=None,
    )


def apply_idle_official(
    read: EmployeePayrollRead,
    official: Decimal,
    tax: Decimal,
    debt: DebtSummary,
) -> EmployeePayrollRead:
    """Официальная выплата месяца, в котором место НЕ СТОИТ НА ПОСТУ.

    Банк платит по трудовому договору, работает человек или нет
    (task_official_payout_debt), поэтому такой месяц перестал быть полным нулём:
    выплата видна, на неё начисляется налог (решение заказчика 25.09.2026 — «на
    все официальные выплаты начисляется налог»), а сама выплата целиком уходит
    в долг.

    «К выплате» берётся у состояния долга: банковский платёж гасится из того,
    что месяц реально даёт кассой. Начислений вахты в нём нет, поэтому обычно
    это ноль; если на месте остались премии/KPI общей системы (заводить их
    запрещено, но наследство есть), они участвуют в гашении — их учитывает
    `official_debt_history._other_accruals`.

    Начисленное (`total_amount`) остаётся нулевым, поэтому «Итого начислено» не
    меняется; база распределения растёт РОВНО на налог — это и есть затрата
    компании за такой месяц.
    """
    return read.model_copy(update={
        # Банковский платёж ДОБАВЛЯЕТСЯ к удержаниям строки, а не заменяет их:
        # аванс и удержание займа, если они на месте остались наследством,
        # обязаны сохраниться — иначе деньги считались бы удержанными, не
        # будучи удержанными (нашло ревью).
        "advance_deduction": read.advance_deduction + official,
        "total_deductions": read.total_deductions + official,
        "guard_tax_amount": tax,
        "net_payout": debt.payout,
        "net_payout_exact": debt.payout,
        "rounding_tail": _ZERO,
        "official_debt_before": debt.debt_before,
        "official_debt_after": debt.debt_after,
        "official_debt_repaid": debt.repaid,
        "is_guard_row": True,
    })


def guard_hours_per_shift() -> int:
    """Смена вахты равна суткам — 24 часа."""
    return HOURS_PER_SHIFT
