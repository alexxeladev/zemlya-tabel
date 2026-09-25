"""
Сборка истории долга по официальной выплате (task_official_payout_debt).

Чистое правило живёт в `official_debt.py`, здесь — только выборка данных: какие
половины были у рабочего места, что в них начислено, сколько ушло через банк и
какой у месяца статус. Ровно то же разделение, что у займа: чистый
`payout.loan_month_state` и сборка `payroll_statement._loan_state`.

**Старт — первый месяц НА ПОСТУ** (решение заказчика 25.09.2026): до первой
строки вахты человека в модуле нет, и долг копить не с чего. Иначе признак
«официально устроен» действует «с начала» (версия условий от 1900 года), и
система насчитала бы долг за годы назад.

**Закрытый месяц со снимком** — факт: долг берётся из `PeriodSnapshot.
official_debt_facts` и не пересчитывается. **Закрытый месяц без снимка**
пропускается целиком: закрытый период не должен прирастать долгом задним
числом (на деве это январь–июнь 2026 — закрыты bulk-ом демо-сида, снимков нет).
"""
from __future__ import annotations

import datetime
from calendar import monthrange
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy.orm import Session, load_only, selectinload

from app.models.guard_assignments import GUARD_HALVES, GuardAssignment
from app.models.period_snapshots import PeriodSnapshot
from app.models.positions import EmployeePosition
from app.models.timesheet_periods import TimesheetPeriod
from app.services.official_debt import (
    STATUS_FACT,
    STATUS_OPEN,
    STATUS_SKIP,
    DebtPeriodInput,
    OfficialDebtState,
    facts_from_snapshot,
    official_debt_state,
)

_ZERO = Decimal("0")

#: Закрытый период — единственный статус, при котором долг не пересчитывается.
#: «На проверке» снимка не имеет и считается живьём, как и черновик.
_CLOSED = "closed"


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _months_between(start: tuple[int, int], end: tuple[int, int]):
    """Месяцы от start до end включительно, по возрастанию."""
    index, last = _month_index(*start), _month_index(*end)
    while index <= last:
        yield divmod(index, 12)[0], divmod(index, 12)[1] + 1
        index += 1


def official_debt_states(
    db: Session,
    positions: Iterable[EmployeePosition],
    year: int,
    month: int,
) -> dict[int, OfficialDebtState]:
    """{position_id: состояние долга} на конец указанного месяца.

    Считается пересчётом истории от первого месяца на посту — состояние нигде
    не хранится (главное требование задачи).
    """
    from app.services.guard_duty import official_month_payouts
    from app.services.guard_month import calculate_assignment

    by_id = {p.id: p for p in positions if p is not None}
    if not by_id:
        return {}

    # Все строки вахты этих мест ДО расчётного месяца включительно — одним
    # запросом: история считается по ним, запрос на месяц был бы N+1.
    assignments = (
        db.query(GuardAssignment)
        .options(selectinload(GuardAssignment.shifts))
        .filter(
            GuardAssignment.position_id.in_(list(by_id)),
            GuardAssignment.year * 12 + GuardAssignment.month
            <= year * 12 + month,
        )
        .order_by(GuardAssignment.id)
        .all()
    )
    if not assignments:
        return {}

    rows: dict[int, dict[tuple[int, int], list[GuardAssignment]]] = {}
    for assignment in assignments:
        rows.setdefault(assignment.position_id, {}).setdefault(
            (assignment.year, assignment.month), []
        ).append(assignment)

    # Начисления ОБЩЕЙ системы (премия, KPI, аванс) на охранном месте — это
    # наследство: заводить их запрещено (`guard_staff`). Но в месяце БЕЗ поста
    # строка идёт общим путём и такие деньги реально выдаются кассой, поэтому в
    # гашении долга они участвуют. В месяце С ПОСТОМ строку считает вахта, её
    # премии — свои, а начисления общей системы в кассу не идут (нашло ревью).
    others = _other_accruals(db, by_id)

    department_ids = {
        p.department_id for p in by_id.values() if p.department_id is not None
    }
    statuses = _period_statuses(db, department_ids)
    facts = _debt_facts(db, list(by_id), (year, month))

    result: dict[int, OfficialDebtState] = {}
    for position_id, months in rows.items():
        position = by_id[position_id]
        start = min(months)
        periods: list[DebtPeriodInput] = []
        for y, m in _months_between(start, (year, month)):
            payouts = official_month_payouts(position, y, m)
            month_rows = months.get((y, m), [])
            accrued = _accrued_by_half(month_rows, calculate_assignment)
            if not month_rows:
                # Месяц без поста: касса выдаёт только начисления общей системы.
                # Относим их к ПЕРВОЙ половине — первой выплате месяца.
                accrued[GUARD_HALVES[0]] += others.get((position_id, y, m), _ZERO)
            status, month_facts = _month_status(
                statuses, facts, position.department_id, position_id, y, m
            )
            for half in GUARD_HALVES:
                periods.append(DebtPeriodInput(
                    year=y, month=m, half=half,
                    accrued=accrued.get(half, _ZERO),
                    official=payouts.get(half, _ZERO),
                    status=status,
                    fact_debt_after=month_facts.get(half),
                ))
        result[position_id] = official_debt_state(periods)
    return result


def _accrued_by_half(assignments, calculate) -> dict[int, Decimal]:
    """Начислено по половинам за месяц: зарплата + премия − штраф.

    Налог и официальная выплата здесь не нужны — долг их не меняет, а «Итого
    начислено» от них не зависит. Ставка налога поэтому передаётся нулевой.
    """
    totals: dict[int, Decimal] = dict.fromkeys(GUARD_HALVES, _ZERO)
    for assignment in assignments:
        result = calculate(assignment, tax_percent=_ZERO)
        for half, value in result.halves.items():
            totals[half] = totals.get(half, _ZERO) + value.accrued
    return totals


def _period_statuses(
    db: Session, department_ids: set[int]
) -> dict[tuple[int, int, int], str]:
    """{(отдел, год, месяц): статус периода табеля}."""
    if not department_ids:
        return {}
    query = db.query(TimesheetPeriod).options(load_only(
        TimesheetPeriod.department_id, TimesheetPeriod.year,
        TimesheetPeriod.month, TimesheetPeriod.status,
    )).filter(TimesheetPeriod.department_id.in_(department_ids))
    return {
        (p.department_id, p.year, p.month): p.status for p in query
    }


def _debt_facts(
    db: Session, position_ids: list[int], last: tuple[int, int]
) -> dict[int, dict[tuple[int, int], dict[int, Decimal]]]:
    """{position_id: {(год, месяц): {половина: долг после}}} из снимков.

    Берутся снимки не позже расчётного месяца и только те, где факты долга есть
    вообще: грузить JSON всех снимков базы здесь нельзя — на этом уже обжигались
    в этапе 3 («первая версия была МЕДЛЕННЕЕ живого расчёта»). По ОТДЕЛУ не
    фильтруем: снимок лежит у отдела, который закрывал период, а место могло с
    тех пор сменить отдел — факт нашёлся бы не всегда (нашло ревью).
    """
    wanted = {str(pid) for pid in position_ids}
    out: dict[int, dict[tuple[int, int], dict[int, Decimal]]] = {}
    query = db.query(PeriodSnapshot).options(load_only(
        PeriodSnapshot.year, PeriodSnapshot.month, PeriodSnapshot.official_debt_facts,
    )).filter(
        PeriodSnapshot.year * 12 + PeriodSnapshot.month <= last[0] * 12 + last[1],
        PeriodSnapshot.official_debt_facts.isnot(None),
    )
    for snap in query:
        for pid, halves in (snap.official_debt_facts or {}).items():
            if pid not in wanted:
                continue
            out.setdefault(int(pid), {})[(snap.year, snap.month)] = (
                facts_from_snapshot(halves)
            )
    return out


def _month_status(
    statuses: dict[tuple[int, int, int], str],
    facts: dict[int, dict[tuple[int, int], dict[int, Decimal]]],
    department_id: int | None,
    position_id: int,
    year: int,
    month: int,
) -> tuple[str, dict[int, Decimal]]:
    """Статус половин месяца и факты долга, если месяц закрыт со снимком."""
    if department_id is None:
        return STATUS_OPEN, {}
    if statuses.get((department_id, year, month)) != _CLOSED:
        return STATUS_OPEN, {}
    month_facts = facts.get(position_id, {}).get((year, month))
    if month_facts:
        return STATUS_FACT, month_facts
    # Закрыт, а снимка нет — пропускаем: задним числом долг не растёт.
    return STATUS_SKIP, {}


def debt_status(
    db: Session, employee, year: int, month: int
) -> list[dict]:
    """Долг по официальной выплате для карточки сотрудника.

    Строка на каждое ОХРАННОЕ рабочее место с историей на посту (обычно одна:
    официальная ставка у охранника одна, и практически это один человек —
    решение заказчика об уровне накопления).

    **Увольнение:** когда рабочее место закрыто (дата увольнения места или
    самого человека уже прошла), непогашенный остаток БОЛЬШЕ НЕ ГАСИТСЯ — гасить
    его нечем, выплат нет. Тогда он и есть зафиксированная задолженность, а
    `closed_on` говорит, на какую дату она зафиксирована: взыскивают её вне
    системы (решение заказчика).
    """
    from app.services.guard_staff import is_guard_position

    positions = [
        p for p in employee.positions if is_guard_position(db, p)
    ]
    states = official_debt_states(db, positions, year, month)
    out: list[dict] = []
    for position in positions:
        state = states.get(position.id)
        if state is None or not state.periods:
            continue
        closed_on = _closed_on(employee, position, year, month)
        out.append({
            "position_id": position.id,
            "position_title": position.title,
            "debt": state.debt_now,
            "debt_before_month": state.periods[0].debt_before,
            "repaid_in_month": sum(
                (p.repaid for p in state.month_periods(year, month)), _ZERO
            ),
            "closed_on": closed_on,
            "is_final": closed_on is not None,
        })
    return out


def _closed_on(employee, position, year: int, month: int):
    """Дата, после которой гасить долг нечем: увольнение места или человека.

    Дата в БУДУЩЕМ (позже расчётного месяца) закрытием не считается: человек
    ещё работает, выплаты идут, и «остаток зафиксирован» было бы неправдой
    (нашло ревью).
    """
    dates = [d for d in (position.dismissal_date, employee.dismissal_date) if d]
    if not dates:
        return None
    closed = min(dates)
    last_day = monthrange(year, month)[1]
    return closed if closed <= datetime.date(year, month, last_day) else None


def _other_accruals(
    db: Session, positions: dict[int, EmployeePosition]
) -> dict[tuple[int, int, int], Decimal]:
    """{(позиция, год, месяц): чем месяц БЕЗ поста может гасить долг}.

    В месяце без поста строка ведомости идёт общим путём, и из кассы реально
    выдаётся `премия + KPI − аванс − удержание займа`. Ровно столько долга он и
    может погасить — иначе долг «гасился» бы деньгами, которых человек не
    получил (нашло ревью).

    Записи с `position_id IS NULL` — доположенческие (миграция `f1a2b3c4d5e6`):
    ведомость относит их к ОСНОВНОЙ позиции, поэтому и здесь они считаются ей,
    иначе премия попадала бы в кассу, но не в гашение.

    Удержание займа берётся ПЛАНОВОЙ долей (сумма ÷ срок) за месяцы внутри
    срока: точное удержание считает ведомость, и спрашивать её здесь нельзя —
    она сама зависит от этого расчёта. Приближение расходится с фактом только
    в месяце, где начисления не хватило на полную долю.
    """
    from app.models.employee_adjustments import EmployeeAdjustment

    if not positions:
        return {}
    employees = {p.employee_id: p for p in positions.values() if p.employee_id}
    primary_of: dict[int, int] = {}
    for position in positions.values():
        employee = position.employee
        if employee is None:
            continue
        primary = next((p for p in employee.positions if p.is_primary), None)
        if primary is not None and primary.id in positions:
            primary_of[employee.id] = primary.id

    out: dict[tuple[int, int, int], Decimal] = {}
    rows = (
        db.query(
            EmployeeAdjustment.employee_id, EmployeeAdjustment.position_id,
            EmployeeAdjustment.year, EmployeeAdjustment.month,
            EmployeeAdjustment.kind, EmployeeAdjustment.amount,
        )
        .filter(EmployeeAdjustment.employee_id.in_(
            [p.employee_id for p in positions.values() if p.employee_id]
        ))
        .all()
    )
    for employee_id, position_id, year, month, kind, amount in rows:
        target = position_id if position_id is not None else primary_of.get(employee_id)
        if target not in positions:
            continue
        sign = -1 if kind == "advance" else 1
        key = (target, year, month)
        out[key] = out.get(key, _ZERO) + sign * Decimal(str(amount))

    # Плановая доля займа месяца: она же уменьшает кассу в общем пути ведомости.
    for employee_id, position in employees.items():
        employee = position.employee
        if employee is None or not employee.loan_amount or not employee.loan_term_months:
            continue
        if not employee.loan_start_date:
            continue
        from app.services.guard_staff import loan_position

        target = loan_position(employee)
        if target is None or target.id not in positions:
            continue
        share = (Decimal(str(employee.loan_amount))
                 / Decimal(employee.loan_term_months)).quantize(Decimal("0.01"))
        start = (employee.loan_start_date.year, employee.loan_start_date.month)
        for offset in range(employee.loan_term_months):
            index = start[0] * 12 + (start[1] - 1) + offset
            key = (target.id, index // 12, index % 12 + 1)
            out[key] = out.get(key, _ZERO) - share
    return out
