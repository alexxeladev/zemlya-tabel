"""
Сводная ведомость «Расчёт ЗП» (задача 3.11b).

Использует ТОТ ЖЕ расчёт что табель (calculate_employee_payroll + compute_payout),
поверх него — управленческое распределение между юрлицами в %.
Распределение: дефолт из карточки сотрудника + помесячное переопределение (гибрид
как у займа).

База распределения — «Итого начислено», и только оно (`distribution_base`).
Распределение отражает ЗАТРАТЫ компании на сотрудника, а они возникают при
НАЧИСЛЕНИИ: удержания (займ, аванс) — возврат ранее выданных средств и затраты
не уменьшают, округление «К выплате» на распределение не влияет вовсе.
В task_it_arm_distribution ч.2 базой ошибочно сделали «К выплате» — откачено
task_distribution_base_fix.

Сами суммы по юрлицам округляются ВНИЗ до ТЫСЯЧИ (ч.3) методом floor + раздача
недостающих тысяч по наибольшим хвостам, поэтому Σ долей НИКОГДА не превышает
начисленного, а разница 0…999 ₽ остаётся нераспределённым остатком
(`unallocated_remainder`) и не приписывается ни одному юрлицу.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.payroll import (
    CompanyBreakdownRead,
    EmployeePayrollRead,
    PayrollSummaryRead,
)
from app.schemas.payroll_statement import (
    PayrollStatementRead,
    StatementCompanyAmount,
    StatementCompanyRef,
    StatementRow,
)
from app.schemas.quantity import QuantityDistributionRow
from app.services.absences import (
    get_month_absences,
    schedules_by_employee,
    sick_days_used_before_month,
)
from app.services.company_order import (
    company_display_name,
    company_order_by,
    order_index,
)
from app.services.company_shares import (
    load_department_shares,
    load_employee_shares,
    load_month_overrides,
)
from app.services.distribution import distribute_largest_remainder
from app.services.guard_payroll import distribute_guard_amount
from app.services.guard_staff import guard_position_ids
from app.services.guard_statement import (
    SOURCE_GUARD_POST,
    guard_idle_payroll,
    guard_payroll_read,
    load_guard_rows,
)
from app.services.night_shifts import load_night_context
from app.services.payout import (
    LoanMonth,
    compute_payout,
    load_adjustment_reasons,
    load_adjustment_sums,
    load_loan_overrides,
    load_targeted_funding,
    loan_capacity,
    loan_month_state,
)
from app.services.payroll import calculate_position_payroll
from app.services.period_snapshots import (
    MonthSnapshots,
    load_month_snapshots,
    loan_facts,
    scope_departments,
)
from app.services.position_terms import month_bounds, terms_on
from app.services.positions import (
    NO_DEPARTMENT,
    NO_DEPARTMENT_LABEL,
    DepartmentFilter,
    entries_by_position,
    visible_positions,
)
from app.services.quantity_distribution import (
    load_quantity_counts,
    quantity_department_ids,
    quantity_percents,
    quantity_weights,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Payroll summary (единый источник для табеля и ведомости) ──────────────────

def _payroll_rows(
    employees: list[Employee],
    actor: Employee | None,
    department_id: DepartmentFilter,
) -> list[tuple[Employee, EmployeePosition | None]]:
    """Строки расчёта: по одной на видимую позицию сотрудника.

    Без actor-а (внутренние вызовы, тесты) берутся все активные позиции. У
    сотрудника без единой позиции строка всё равно нужна — иначе он молча
    исчезнет из ведомости; считается она как «нет графика».
    """
    rows: list[tuple[Employee, EmployeePosition | None]] = []
    for emp in employees:
        positions = (
            visible_positions(emp, actor, department_id)
            if actor is not None
            else emp.active_positions
        )
        if not positions:
            # Ни одной видимой/активной позиции — строка всё равно нужна, иначе
            # сотрудник молча пропадёт из ведомости.
            rows.append((emp, emp.primary_position))
        else:
            rows.extend((emp, pos) for pos in positions)
    return rows


def _loan_belongs_to(employee: Employee, position: EmployeePosition | None) -> bool:
    """Займ гасится с ОДНОЙ позиции — той, что указана в карточке.

    `loan_position_id` пуст у займов, заведённых до появления позиций, — они
    удерживаются с основной, как и раньше.
    """
    if employee.loan_amount is None or position is None:
        return False
    if employee.loan_position_id is None:
        return bool(position.is_primary)
    return employee.loan_position_id == position.id


# ── Заём при нулевом начислении (п.5.1) ───────────────────────────────────────
#
# Удержать можно только то, что начислено позиции займа за месяц (минус аванс):
# нечего удерживать — месяц пропускается, остаток не уменьшается, срок займа
# растягивается. Правило действует в месяцах, которые система ВЕЛА и которые не
# закрыты: закрытый месяц со снимком — факт из снимка, закрытый без снимка и
# месяц до первого периода отдела — доля удержана по графику, как раньше
# (решения заказчика 24.09.2026).

def _period_statuses(
    db: Session, department_ids: set[int | None], last: tuple[int, int],
) -> tuple[dict[tuple[int | None, int, int], str], dict[int | None, int]]:
    """Периоды отделов по месяц `last` включительно, одним запросом:
    ({(отдел, год, месяц): статус}, {отдел: индекс месяца его ПЕРВОГО периода}).

    Первый период — начало учёта отдела в системе: раньше него доля займа
    считается удержанной по графику (решение заказчика 24.09.2026). Периоды
    создаются лениво, поэтому «нет строки периода» у месяца ПОСЛЕ первого —
    это просто месяц, который ещё никто не открывал, а не «до системы».
    """
    if not department_ids:
        return {}, {}
    ids = [d for d in department_ids if d is not None]
    cond = []
    if ids:
        cond.append(TimesheetPeriod.department_id.in_(ids))
    if None in department_ids:
        cond.append(TimesheetPeriod.department_id.is_(None))
    hi = last[0] * 12 + last[1] - 1
    rows = (
        db.query(TimesheetPeriod.department_id, TimesheetPeriod.year,
                 TimesheetPeriod.month, TimesheetPeriod.status)
        .filter(or_(*cond))
        .filter(TimesheetPeriod.year * 12 + TimesheetPeriod.month - 1 <= hi)
        .all()
    )
    statuses: dict[tuple[int | None, int, int], str] = {}
    first: dict[int | None, int] = {}
    for d, y, m, st in rows:
        statuses[(d, y, m)] = st
        idx = y * 12 + m - 1
        if d not in first or idx < first[d]:
            first[d] = idx
    return statuses, first


def _under_zero_accrual_rule(
    statuses: dict[tuple[int | None, int, int], str],
    first: dict[int | None, int],
    department_id: int | None,
    y: int,
    m: int,
) -> bool:
    """Действует ли в месяце правило п.5.1: отдел уже вёл учёт в системе
    (месяц не раньше его первого периода) и месяц не закрыт. Одно условие и
    для истории, и для расчётного месяца — иначе остаток одного займа на
    разных экранах разойдётся."""
    start = first.get(department_id)
    if start is None or y * 12 + m - 1 < start:
        return False
    return statuses.get((department_id, y, m)) != "closed"


def _loan_capacities(
    db: Session,
    loan_rows: list[tuple[Employee, EmployeePosition]],
    year: int,
    month: int,
    loan_overrides: dict[int, dict[tuple[int, int], Decimal]],
    closed_loans: dict[int, dict[tuple[int, int], Decimal]],
) -> tuple[dict[int, dict[tuple[int, int], Decimal]], set[int | None]]:
    """({position_id: {(год, месяц): сколько можно удержать}}, отделы, у которых
    РАСЧЁТНЫЙ месяц НЕ под правилом п.5.1 — закрыт или раньше начала учёта).

    Первое — по месяцам ДО расчётного, где действует правило п.5.1
    (`_under_zero_accrual_rule`) и удержание не зафиксировано правкой или
    снимком.
    Начисление прошлого месяца считается тем же `build_payroll_summary` — своей
    формулы здесь нет. Один расчёт на месяц для всех, кому он нужен, и только
    по позициям займа. Статусы периодов — одним запросом на весь диапазон.
    """
    if not loan_rows:
        return {}, set()
    target_idx = year * 12 + month - 1
    departments = {pos.department_id for _, pos in loan_rows}
    statuses, first = _period_statuses(db, departments, (year, month))
    old_rule_now = {
        d for d in departments
        if not _under_zero_accrual_rule(statuses, first, d, year, month)
    }
    need: dict[tuple[int, int], dict[int, Employee]] = {}
    for emp, pos in loan_rows:
        start = emp.loan_start_date.year * 12 + emp.loan_start_date.month - 1
        fixed = set(loan_overrides.get(emp.id, {})) | set(closed_loans.get(pos.id, {}))
        for idx in range(start, target_idx):
            y, m = idx // 12, idx % 12 + 1
            if (y, m) in fixed:
                continue
            if not _under_zero_accrual_rule(statuses, first, pos.department_id, y, m):
                continue
            need.setdefault((y, m), {})[emp.id] = emp

    from app.services.timesheet import get_month_entries

    loan_pos = {emp.id: pos.id for emp, pos in loan_rows}
    result: dict[int, dict[tuple[int, int], Decimal]] = {}
    for (y, m), by_id in sorted(need.items()):
        emps = list(by_id.values())
        out: dict[int, Decimal] = {loan_pos[e.id]: _ZERO for e in emps}
        # Снимков у этих позиций в месяце нет (он не закрыт) — не грузим.
        build_payroll_summary(
            db, emps, get_month_entries(db, emps, y, m), y, m,
            snapshots=MonthSnapshots([]), _loan_capacity_out=out,
        )
        for emp in emps:
            pid = loan_pos[emp.id]
            # Нет строки позиции в том месяце (место снято с учёта) — удержать
            # было не с чего.
            result.setdefault(pid, {})[(y, m)] = out.get(pid, _ZERO)
    return result, old_rule_now


def _loan_state(
    emp: Employee,
    position: EmployeePosition,
    year: int,
    month: int,
    loan_overrides: dict[int, dict[tuple[int, int], Decimal]],
    closed_loans: dict[int, dict[tuple[int, int], Decimal]],
    history: dict[int, dict[tuple[int, int], Decimal]],
    target_capacity: Decimal | None,
) -> LoanMonth | None:
    """Состояние займа позиции на месяц: ручные правки + факт закрытых месяцев
    (снимок) + сколько можно было удержать в открытых. `target_capacity` None —
    расчётный месяц не под правилом п.5.1 (закрыт без снимка или раньше начала
    учёта отдела) и считается по-старому."""
    capacities = dict(history.get(position.id, {}))
    if target_capacity is not None:
        capacities[(year, month)] = target_capacity
    return loan_month_state(
        emp.loan_amount, emp.loan_term_months, emp.loan_start_date,
        year, month, loan_overrides.get(emp.id) or {}, capacities,
        closed_loans.get(position.id, {}),
    )


def loan_status(db: Session, emp: Employee, year: int, month: int) -> LoanMonth | None:
    """Состояние займа сотрудника на месяц — для карточки («осталось платежей»)
    и ручной правки удержания. Те же части, что в ведомости (`_loan_capacities`,
    `_loan_state`), но и для ЗАКРЫТОГО месяца: там удержание — факт снимка,
    а строку ведомость берёт из снимка и займ не считает. Начисление открытого
    месяца — тот же `build_payroll_summary` в служебном режиме. `None` — займа
    нет или месяц раньше его старта."""
    from app.services.guard_staff import loan_position
    from app.services.timesheet import get_month_entries

    position = loan_position(emp)
    if (position is None or emp.loan_amount is None
            or not emp.loan_term_months or not emp.loan_start_date):
        return None
    overrides = load_loan_overrides(db, [emp.id])
    facts = loan_facts(db)
    history, old_rule_now = _loan_capacities(
        db, [(emp, position)], year, month, overrides, facts
    )
    target_capacity = None
    if (position.department_id not in old_rule_now
            and (year, month) not in facts.get(position.id, {})):
        out: dict[int, Decimal] = {position.id: _ZERO}
        build_payroll_summary(
            db, [emp], get_month_entries(db, [emp], year, month), year, month,
            snapshots=MonthSnapshots([]), _loan_capacity_out=out,
        )
        target_capacity = out[position.id]
    return _loan_state(
        emp, position, year, month, overrides, facts, history, target_capacity,
    )


def build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee | None = None,
    department_id: DepartmentFilter = None,
    snapshots: MonthSnapshots | None = None,
    _loan_capacity_out: dict[int, Decimal] | None = None,
) -> PayrollSummaryRead:
    """Сводный расчёт ЗП — ОДНА СТРОКА НА ПОЗИЦИЮ (task_positions ч.A).

    Единый источник: используется и табелем (/payroll), и ведомостью (/statement).

    У сотрудника без совместительства позиция одна, и выдача совпадает с прежней
    построчно. У совместителя строк столько, сколько рабочих мест: каждое со
    своим окладом, графиком и нормой, а «к выплате» между ними НЕ суммируется —
    платят разные компании.

    actor/department_id — чтобы менеджер не увидел подработку сотрудника в чужом
    отделе; без actor берутся все активные позиции.

    `_loan_capacity_out` — служебный режим истории займа (п.5.1): считаются
    только позиции-ключи словаря, заём не применяется, а в словарь пишется,
    сколько можно было удержать с каждой (`_loan_capacities`).
    """
    history_mode = _loan_capacity_out is not None
    # Снимки закрытых периодов месяца (task_stage3_historicity): позиция со
    # строкой в снимке берётся оттуда, живой расчёт для неё не делается.
    snaps = (
        snapshots if snapshots is not None
        else load_month_snapshots(db, year, month, scope_departments(actor, department_id))
    )
    all_rows = _payroll_rows(employees, actor, department_id)
    if history_mode:
        all_rows = [(e, p) for e, p in all_rows if p is not None and p.id in _loan_capacity_out]
    snapshot_emp_ids = {emp.id for emp in employees}
    live_employees = list({
        emp.id: emp for emp, pos in all_rows
        if not (pos is not None and snaps.has(pos.id))
    }.values())
    # Всё тяжёлое ниже — только для людей с живыми строками: закрытый отдел
    # читается из снимка и не должен стоить пересчёта.
    employees = live_employees

    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    calendar_data = cal.data if cal else None

    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(*company_order_by()).all()
    )
    companies_by_id = {c.id: (c.code, c.name) for c in companies}

    entries_by_employee: dict[int, list] = {}
    for e in entries:
        entries_by_employee.setdefault(e.employee_id, []).append(e)

    # Отсутствия месяца (ОТ/ДО/Б/Н) — дают отпускные/больничные и уменьшают
    # оклад пропорционально (в дне отсутствия часов нет).
    absences_by_employee: dict[int, list] = {}
    for a in get_month_absences(db, employees, year, month):
        absences_by_employee.setdefault(a.employee_id, []).append(a)

    emp_ids = [emp.id for emp in employees]
    primary_position_ids = {
        emp.id: (emp.primary_position.id if emp.primary_position else None)
        for emp in employees
    }
    adjustment_sums = load_adjustment_sums(
        db, emp_ids, year, month, primary_position_ids
    )
    loan_overrides = load_loan_overrides(db, emp_ids) if not history_mode else {}
    # Удержания займа закрытых месяцев — факт из снимков (решение заказчика):
    # новые условия займа раскладываются только на открытые месяцы.
    closed_loans = loan_facts(db) if emp_ids and not history_mode else {}
    # Годовой лимит больничного: сколько оплачиваемых дней Б уже израсходовано
    # с 1 января до этого месяца (часть 2).
    sick_used_before = sick_days_used_before_month(
        db, emp_ids, year, month, calendar_data, schedules_by_employee(employees)
    )
    # Ночные смены: сколько отмечено на каждом рабочем месте и почём (ставка =
    # фонд отдела / календарные дни месяца). Надбавка идёт сверху дневного
    # расчёта — см. `services.night_shifts`.
    night = load_night_context(db, employees, year, month)

    # Вахта (task_vahta): позиция, у которой в этом месяце есть назначение на
    # пост охраны, считается МОДУЛЕМ ВАХТЫ, а не общим расчётом — часов в табеле
    # у охранника нет, вся его работа это отметки смен. Строки остальных
    # подразделений эта ветка не касается: словарь для них пуст.
    guard_rows = load_guard_rows(
        db,
        [pos.id for emp in employees for pos in emp.positions],
        year, month,
    )
    # Охранное рабочее место БЕЗ строки вахты в месяце — «не на посту», 0 ₽:
    # в общий расчёт его не пускает предикат `guard_staff`, а не отсутствие
    # графика (task_guard_form_rate_official). Ставки на охранной позиции нет.
    guard_positions = guard_position_ids(
        db, [pos for emp in employees for pos in emp.positions]
    )

    # Заём (п.5.1): сколько можно было удержать в прошлых открытых месяцах —
    # от этого зависит остаток. И статус расчётного месяца: закрытый без
    # снимка считается по-старому (его цифры не должны поехать).
    loan_rows = [] if history_mode else [
        (emp, pos) for emp, pos in all_rows
        if pos is not None and not snaps.has(pos.id) and pos.id not in guard_rows
        and _loan_belongs_to(emp, pos)
        and emp.loan_term_months and emp.loan_start_date
    ]
    loan_history, old_rule_now = _loan_capacities(
        db, loan_rows, year, month, loan_overrides, closed_loans
    )

    payroll_items: list[EmployeePayrollRead] = []
    taken_from_snapshot: set[int] = set()
    for emp, position in all_rows:
        if position is not None and snaps.has(position.id):
            payroll_items.append(EmployeePayrollRead.model_validate(snaps.payroll[position.id]))
            taken_from_snapshot.add(position.id)
            continue
        guard_row = guard_rows.get(position.id) if position is not None else None
        if guard_row is not None:
            if history_mode:
                # Строка вахты заём не удерживает вовсе — удержать было не с чего.
                _loan_capacity_out[position.id] = _ZERO
            payroll_items.append(
                guard_payroll_read(emp, position, guard_row, year, month)
            )
            continue
        # Охранное место без строки вахты — «не на посту»: ЗАРАБОТОК ноль, а
        # премии/KPI/аванс и заём ниже применяются как всем (они адресованы
        # рабочему месту, а не посту).
        idle_guard = position is not None and position.id in guard_positions
        if idle_guard:
            p = guard_idle_payroll(emp, position)
        else:
            # Часы позиции: строки без position_id — доположенческие, они
            # принадлежат основной позиции (иначе миграция потеряла бы часы).
            by_position = entries_by_position(emp, entries_by_employee.get(emp.id, []))
            emp_entries = by_position.get(position.id, []) if position is not None else []
            p = calculate_position_payroll(
                emp, position, emp_entries, calendar_data, year, month, companies_by_id,
                absences=absences_by_employee.get(emp.id, []),
                sick_days_used_before=sick_used_before.get(emp.id, 0),
                night_shifts=night.shifts_of(position),
                night_rate=night.rate_of(position),
            )

        # Премии/KPI/аванс и займ адресованы КОНКРЕТНОЙ позиции: деньги
        # начисляются человеку, но попадают в «к выплате» того рабочего места,
        # на котором заработаны.
        position_id = position.id if position is not None else None
        sums = adjustment_sums.get(emp.id, {}).get(position_id, {})
        capacity = loan_capacity(
            p.total_amount + sums.get("premium", _ZERO) + sums.get("kpi", _ZERO),
            sums.get("advance", _ZERO),
        )
        loan_state = None
        if history_mode:
            if position_id is not None:
                _loan_capacity_out[position_id] = capacity
        elif _loan_belongs_to(emp, position):
            loan_state = _loan_state(
                emp, position, year, month, loan_overrides, closed_loans, loan_history,
                None if position.department_id in old_rule_now else capacity,
            )
        loan_deduction = loan_state.actual if loan_state else _ZERO
        payout = compute_payout(
            accrued_total=p.total_amount,
            premium_amount=sums.get("premium", _ZERO),
            kpi_amount=sums.get("kpi", _ZERO),
            advance_deduction=sums.get("advance", _ZERO),
            loan_deduction=loan_deduction,
        )

        month_terms = terms_on(position, month_bounds(year, month)[1]) if position else None
        breakdown = [
            CompanyBreakdownRead(
                company_id=bd.company_id,
                company_code=bd.company_code,
                company_name=bd.company_name,
                hours=bd.hours,
                percent=bd.percent,
                overtime_hours=bd.overtime_hours,
                off_schedule_hours=bd.off_schedule_hours,
                holiday_hours=bd.holiday_hours,
                base_amount=bd.base_amount,
                overtime_amount=bd.overtime_amount,
                off_schedule_amount=bd.off_schedule_amount,
                holiday_amount=bd.holiday_amount,
                total=bd.total,
            )
            for bd in p.breakdown_by_company
        ]
        payroll_items.append(EmployeePayrollRead(
            employee_id=p.employee_id,
            employee_name=p.employee_name,
            position_id=p.position_id,
            position_title=p.position_title,
            is_primary_position=p.is_primary_position,
            rate=p.rate,
            schedule_name=p.schedule_name,
            pay_type=p.pay_type,
            shift_rate=p.shift_rate,
            hour_rate=p.hour_rate,
            worked_shifts=p.worked_shifts,
            norm_shifts=p.norm_shifts,
            base_shifts=p.base_shifts,
            total_hours=p.total_hours,
            norm_hours=p.norm_hours,
            delta_hours=p.delta_hours,
            overtime_hours=p.overtime_hours,
            off_schedule_hours=p.off_schedule_hours,
            holiday_hours=p.holiday_hours,
            norm_days=p.norm_days,
            fact_days=p.fact_days,
            hourly_rate=p.hourly_rate,
            base_amount=p.base_amount,
            overtime_amount=p.overtime_amount,
            off_schedule_amount=p.off_schedule_amount,
            holiday_amount=p.holiday_amount,
            total_amount=p.total_amount,
            night_shifts=p.night_shifts,
            night_rate=p.night_rate,
            night_amount=p.night_amount,
            vacation_days=p.vacation_days,
            unpaid_days=p.unpaid_days,
            sick_days=p.sick_days,
            absent_days=p.absent_days,
            vacation_paid_days=p.vacation_paid_days,
            sick_paid_days=p.sick_paid_days,
            vacation_amount=p.vacation_amount,
            sick_amount=p.sick_amount,
            sick_limit_days=p.sick_limit_days,
            sick_days_used_before=p.sick_days_used_before,
            sick_unpaid_days=p.sick_unpaid_days,
            sick_limit_remaining=p.sick_limit_remaining,
            # Коэффициенты для колонки «Коэф.» — версии условий на конец месяца
            # (task_stage3_historicity), а не текущие поля карточки.
            weekend_pay_type=month_terms.weekend_pay_type if position else None,
            weekend_coefficient=month_terms.weekend_coefficient if position else None,
            weekend_fixed_rate=month_terms.weekend_fixed_rate if position else None,
            holiday_pay_type=month_terms.holiday_pay_type if position else None,
            holiday_coefficient=month_terms.holiday_coefficient if position else None,
            holiday_fixed_rate=month_terms.holiday_fixed_rate if position else None,
            premium_amount=payout.premium_amount,
            kpi_amount=payout.kpi_amount,
            advance_deduction=payout.advance_deduction,
            loan_deduction=payout.loan_deduction,
            loan_remaining=loan_state.remaining_after if loan_state else _ZERO,
            loan_planned_deduction=loan_state.planned if loan_state else _ZERO,
            loan_is_manual=loan_state.is_manual if loan_state else False,
            loan_shortfall=loan_state.shortfall if loan_state else _ZERO,
            total_deductions=payout.total_deductions,
            net_payout=payout.net_payout,
            net_payout_exact=payout.net_payout_exact,
            rounding_tail=payout.rounding_tail,
            breakdown_by_company=breakdown,
            is_calculable=p.is_calculable,
            reason_if_not_calculable=p.reason_if_not_calculable,
            # Строка охранного места (на посту или «не на посту») помечена как
            # вахтовая: по этому признаку ведомость ищет её расклад по юрлицам.
            is_guard_row=idle_guard,
        ))

    # Строки снимков, которых нет среди текущих позиций (место сняли с учёта
    # после закрытия): закрытый месяц показывает то, что было закрыто.
    for pid in snaps.rows_in_scope(scope_departments(actor, department_id), snapshot_emp_ids):
        if pid not in taken_from_snapshot:
            payroll_items.append(EmployeePayrollRead.model_validate(snaps.payroll[pid]))

    return PayrollSummaryRead(
        year=year,
        month=month,
        employees=payroll_items,
        total_employees=len(payroll_items),
        total_hours=sum((p.total_hours for p in payroll_items), _ZERO),
        total_base_amount=sum((p.base_amount for p in payroll_items), _ZERO),
        total_overtime_amount=sum((p.overtime_amount for p in payroll_items), _ZERO),
        total_off_schedule_amount=sum((p.off_schedule_amount for p in payroll_items), _ZERO),
        total_holiday_amount=sum((p.holiday_amount for p in payroll_items), _ZERO),
        total_vacation_amount=sum((p.vacation_amount for p in payroll_items), _ZERO),
        total_sick_amount=sum((p.sick_amount for p in payroll_items), _ZERO),
        total_night_amount=sum((p.night_amount for p in payroll_items), _ZERO),
        grand_total=sum((p.total_amount for p in payroll_items), _ZERO),
        total_premium=sum((p.premium_amount for p in payroll_items), _ZERO),
        total_kpi=sum((p.kpi_amount for p in payroll_items), _ZERO),
        total_deductions=sum((p.total_deductions for p in payroll_items), _ZERO),
        # Итог — сумма УЖЕ округлённых выплат, а не округление суммы.
        total_net_payout=sum((p.net_payout for p in payroll_items), _ZERO),
        total_net_payout_exact=sum((p.net_payout_exact for p in payroll_items), _ZERO),
        total_rounding_tail=sum((p.rounding_tail for p in payroll_items), _ZERO),
    )


# ── Распределение по компаниям (проценты) ─────────────────────────────────────

# Загрузка наборов процентов — в app.services.company_shares (общая с роутерами).

# Откуда взято распределение (task_distribution_v2 ч.3) — видно в ведомости и Excel.
SOURCE_MONTH = "month"          # ручной % за конкретный месяц
SOURCE_EMPLOYEE = "employee"    # распределение в карточке сотрудника
SOURCE_DEPARTMENT = "department"  # дефолт отдела
SOURCE_HOURS = "hours"          # авто по фактическим часам табеля
# Отдел с флагом «распределение по количественному показателю» (заявки у HR,
# АРМ у ИТ — task_hr_applications / task_it_arm_distribution) ЗАМЕНЯЕТ каскад
# целиком, поэтому это не ещё один его уровень, а ветка выше него.
SOURCE_QUANTITY = "quantity"


def resolve_shares(
    position_id: int | None,
    department_id: int | None,
    month_overrides: dict[int | None, dict[int, Decimal]],
    employee_shares: dict[int | None, dict[int, Decimal]],
    department_shares: dict[int, dict[int, Decimal]],
) -> tuple[dict[int, Decimal], str]:
    """Каскад приоритетов распределения: берётся ПЕРВОЕ заданное сверху вниз —
    месячный % > карточка (позиция) > дефолт отдела > авто по часам.

    Каскад разрешается для КОНКРЕТНОЙ ПОЗИЦИИ (task_positions ч.A): отдел берётся
    её, проценты — заданные для неё. Дефолт отдела применяется ТОЛЬКО к позициям
    без своего распределения: правка дефолта отдела не трогает тех, у кого задана
    карточка или месяц. Возвращает (проценты, источник). Для авто — пустой набор.
    """
    levels = (
        (month_overrides.get(position_id, {}), SOURCE_MONTH),
        (employee_shares.get(position_id, {}), SOURCE_EMPLOYEE),
        (department_shares.get(department_id, {}) if department_id else {}, SOURCE_DEPARTMENT),
    )
    for shares, source in levels:
        if sum(shares.values(), _ZERO) > _ZERO:
            return shares, source
    return {}, SOURCE_HOURS


def accrued_total(p: EmployeePayrollRead) -> Decimal:
    """«Итого начислено» строки — колонка ведомости и БАЗА распределения затрат
    по юрлицам (см. `distribution_base`).

    Слагаемые перечислены явно и от раскладки по КОЛОНКАМ ведомости не зависят:
    обе повышенные категории (вне графика и праздничные) считаются здесь
    независимо от того, показаны они в «оклад» или в «переработку»
    (task_overtime_columns переложил их во вторую).

    Штраф вахты (`guard_penalty_amount`) вычитается: в вахте «Итого начислено»
    это «зарплата плюс премия МИНУС штраф» (task_vahta). У всех остальных строк
    он ноль, поэтому на обычную ведомость правило не влияет.
    """
    return (
        p.base_amount + p.off_schedule_amount + p.holiday_amount
        + p.overtime_amount + p.vacation_amount + p.sick_amount
        + p.night_amount + p.premium_amount + p.kpi_amount
        - p.guard_penalty_amount
    )


def distribution_base(p: EmployeePayrollRead) -> Decimal:
    """БАЗА распределения по юрлицам — «Итого начислено», и только оно.

    Распределение отражает ЗАТРАТЫ компании на сотрудника, а затраты возникают
    в момент НАЧИСЛЕНИЯ. Удержания (займ, аванс) — возврат ранее выданных
    средств, отдельная операция: затраты на оплату труда они не уменьшают.
    Начислено 100 000 и удержан займ 20 000 — компания потратила 100 000, и
    ровно 100 000 расходится по юрлицам.

    Округление «К выплате» до тысячи (`payout.round_to_payout_step`) на
    распределение НЕ влияет вовсе: это про выплату, а не про затраты.

    В task_it_arm_distribution ч.2 базой ошибочно сделали «К выплате» — при
    удержаниях юрлица недосчитывались затрат. Откачено task_distribution_base_fix.

    Одно место на всю систему: ведомость, блок распределения в табеле и Excel
    обязаны делить одно и то же число, иначе экраны разойдутся.

    **Вахта — уточнение того же принципа, а не исключение** (task_vahta_taxes):
    к затратам на официально устроенного охранника добавляется налоговая
    нагрузка на официальную часть выплаты (`guard_tax_amount` = оф. выплата ×
    ставка из настроек вахты). Поэтому у строки вахты сумма по юрлицам больше
    «Итого начислено» ровно на налог. У всех остальных строк налог ноль — их
    база не меняется ни на рубль.
    """
    return accrued_total(p) + p.guard_tax_amount


def unallocated_remainder(base: Decimal, amounts: dict[int, Decimal]) -> Decimal:
    """Нераспределённый остаток строки = база − Σ разнесённого по юрлицам.

    Суммы по юрлицам кратны тысяче и получены ТОЛЬКО floor-ом
    (`distribute_largest_remainder`), а «Итого начислено» кратным тысяче не
    бывает — отсюда остаток от 0 до 999 ₽. Он никому не приписывается: припиши
    его компании, и её затраты окажутся больше начисленного (переразнесение).

    Не путать с «Эффектом округления» на дашборде: тот — разница между точной и
    округлённой ВЫПЛАТОЙ (`rounding_tail`, знак любой), этот — разница между
    начисленным и разнесённым (всегда ≥ 0).

    Вырожденный случай: разносить не на что (ни одного юрлица с положительным
    весом) — распределения нет вовсе, и остатком оказывается всё начисление.
    Так честнее, чем показать ноль там, где не разнесено ничего.
    """
    return base - sum(amounts.values(), _ZERO)


# ── Источник финансирования премий и KPI (task_funding_source) ────────────────

#: Как называется целевое начисление в пометке ведомости.
_TARGETED_KIND_LABELS = {"premium": "целевую премию", "kpi": "целевой KPI"}


class TargetedFunding:
    """Целевые начисления строки: премии и KPI с указанным юрлицом-источником.

    Такая сумма относится на затраты СВОЕЙ компании целиком, а каскад
    распределения применяется к ОСТАТКУ: `база_каскада = Итого начислено −
    Σ целевых`. Прибавить целевые сверх распределённого «Итого начислено»
    нельзя — распределение перестанет сходиться с начислением (двойной счёт).
    """

    __slots__ = ("amounts", "total", "note", "exceeds_accrued")

    def __init__(
        self,
        amounts: dict[int, Decimal],
        total: Decimal,
        note: str | None,
        exceeds_accrued: bool,
    ):
        self.amounts = amounts
        self.total = total
        self.note = note
        self.exceeds_accrued = exceeds_accrued


EMPTY_TARGETED = TargetedFunding({}, _ZERO, None, False)


def build_targeted_funding(
    items: list[tuple[int, str, Decimal]] | None,
    base: Decimal,
    company_names: dict[int, str],
) -> TargetedFunding:
    """[(company_id, kind, amount)] → суммы по юрлицам, итог и пометка.

    Пометка нужна бухгалтеру: фактический процент юрлица в ведомости из-за
    целевых сумм отличается от заданного в каскаде (задал 50/50 — видит 40/60),
    и без объяснения это выглядит ошибкой.
    """
    if not items:
        return EMPTY_TARGETED
    amounts: dict[int, Decimal] = {}
    notes: list[str] = []
    for company_id, kind, amount in items:
        amounts[company_id] = amounts.get(company_id, _ZERO) + amount
        label = _TARGETED_KIND_LABELS.get(kind, "целевое начисление")
        name = company_names.get(company_id, "")
        notes.append(f"{label} {_fmt_amount(amount)} ₽ ({name})".replace(" ()", ""))
    total = sum(amounts.values(), _ZERO)
    return TargetedFunding(
        amounts=amounts,
        total=total,
        note="включает " + ", ".join(notes),
        # Целевые — часть начисленного, а база распределения — начисленное
        # целиком, поэтому перевесить её они не могут. Проверка оставлена
        # защитой: если это всё же случится, база каскада обнуляется, а сами
        # целевые ужимаются до базы пропорционально — разнести больше, чем
        # начислено, нельзя. Отрицательной база не бывает.
        exceeds_accrued=total > base,
    )


def cascade_base_amount(base: Decimal, targeted: TargetedFunding) -> Decimal:
    """База каскада = база распределения («Итого начислено») − целевые, но не
    меньше нуля."""
    rest = base - targeted.total
    return rest if rest > _ZERO else _ZERO


def merge_targeted(
    cascade_amounts: dict[int, Decimal], targeted: TargetedFunding
) -> dict[int, Decimal]:
    """Итог по юрлицу = доля из каскада + целевые суммы этого юрлица.

    Компания-источник появляется в разбивке даже если в обычном распределении
    сотрудника её не было.
    """
    if not targeted.amounts:
        return cascade_amounts
    merged = dict(cascade_amounts)
    for company_id, amount in targeted.amounts.items():
        merged[company_id] = merged.get(company_id, _ZERO) + amount
    return merged


def effective_percent(amount: Decimal, base: Decimal) -> Decimal:
    """Фактическая доля юрлица в базе распределения («Итого начислено»), в %.

    С целевыми суммами она не совпадает с заданным в каскаде процентом — именно
    её и надо показывать в ведомости (40/60 при каскаде 50/50). Из-за округления
    долей до тысячи она и без целевых может слегка отличаться от заданной.
    """
    if base <= _ZERO:
        return _ZERO
    return (amount / base * _HUNDRED).quantize(Decimal("0.01"))


def _quantity_context(
    db: Session, department_ids: list[int | None], year: int, month: int
) -> tuple[set[int], dict[int, dict[int, int]]]:
    """(отделы с флагом, {department_id: {company_id: количество}} за ЭТОТ месяц).

    Флаг и количества возвращаются РАЗДЕЛЬНО: отдел с флагом, но без показателя
    за месяц — это не «как все», а отдельное состояние (уходит на каскад с
    предупреждением). Оба набора пустые → всё идёт по каскаду.
    """
    flagged = quantity_department_ids(db, [d for d in department_ids if d])
    if not flagged:
        return set(), {}
    return flagged, load_quantity_counts(db, flagged, year, month)


def resolve_quantity_shares(
    position_id: int | None,
    qty_counts: dict[int, int],
    employee_shares: dict[int | None, dict[int, Decimal]],
) -> tuple[dict[int, Decimal], dict[int, Decimal], str]:
    """Распределение рабочего места в отделе «по количественному показателю»:
    КАРТОЧКА ПОЗИЦИИ перебивает показатель (task_card_priority).

    Возвращает (проценты для показа, веса для сумм, источник).

    Задано в карточке — показатель для ЭТОЙ позиции не участвует ВООБЩЕ: это
    полная замена, а не смешивание (складывать карточку с заявками нельзя).
    «Задано» = есть хотя бы одна доля с положительным процентом; набор всегда
    даёт 100% — частичный в карточку не сохранить (`validate_shares`).

    Исключение решается ПО ПОЗИЦИИ, а не по человеку: у совместителя одно
    рабочее место может уйти на карточку, а второе остаться на показателе.
    Остальных сотрудников отдела чужая карточка не касается — проценты
    показателя считаются от количеств отдела, а не от числа делящихся по ним.

    Выше карточки в таком отделе ничего нет: месячная правка в ведомости для
    этих отделов заблокирована, а дефолт отдела показатель заменяет — иначе
    настройка отдела спорила бы с его же показателем.
    """
    card = employee_shares.get(position_id) or {}
    if sum(card.values(), _ZERO) > _ZERO:
        return card, card, SOURCE_EMPLOYEE
    return quantity_percents(qty_counts), quantity_weights(qty_counts), SOURCE_QUANTITY


def _exact_shares(total: Decimal, weights: dict[int, Decimal]) -> dict[int, Decimal]:
    """Точные (НЕокруглённые) доли по весам. Округляется потом весь набор сразу.

    Промежуточного округления быть не должно: целевые суммы прибавляются к долям
    каскада ДО округления, и округляется уже итог по юрлицу (иначе округлили бы
    дважды и Σ разошлась бы с базой).
    """
    positive = {k: w for k, w in weights.items() if w > _ZERO}
    weight_sum = sum(positive.values(), _ZERO)
    if not positive or weight_sum <= _ZERO:
        return {}
    return {k: total * w / weight_sum for k, w in positive.items()}


def finalize_distribution(
    base: Decimal,
    cascade_weights: dict[int, Decimal],
    targeted: TargetedFunding,
    company_order: dict[int, int] | None = None,
) -> dict[int, Decimal]:
    """Суммы по юрлицам: каскад делит базу без целевых, целевые ложатся на свои
    юрлица, и ВЕСЬ набор округляется ВНИЗ до тысячи.

    Σ долей = floor(base / 1000) × 1000, то есть на 0…999 ₽ меньше базы —
    разницу считает `unallocated_remainder`, и она не приписывается никому.
    Округление одно на весь набор (task_it_arm_distribution ч.3): целевые суммы
    участвуют в нём наравне с долями каскада — иначе их округлили бы дважды.
    Тай-брейк равных хвостов — настроенный порядок юрлиц.

    Веса передаются СЫРЫЕ (проценты, количества, часы), а не уже посчитанные
    суммы: при нулевой базе доли обнулились бы, и юрлица молча выпали бы из
    разбивки. Как только появляются целевые, набор приходится собирать из точных
    сумм — иначе целевые не с чем сложить.
    """
    if not targeted.amounts:
        return distribute_largest_remainder(base, cascade_weights, order=company_order)
    exact = merge_targeted(
        _exact_shares(cascade_base_amount(base, targeted), cascade_weights), targeted
    )
    return distribute_largest_remainder(base, exact, order=company_order)


def _auto_shares_by_hours(breakdown, main_company) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """Авто-распределение, когда ручной % не задан: пропорционально фактическим
    часам сотрудника по компаниям (из табеля).

    Возвращает (shares % справочно, weights — часы). Суммы считает общий
    `finalize_distribution`: округление до тысячи одно на все ветки каскада.
    Если часов нет — всё на основную компанию (default_company).
    """
    company_hours = {bd.company_id: bd.hours for bd in breakdown if bd.hours > _ZERO}
    total_hours = sum(company_hours.values(), _ZERO)

    if total_hours > _ZERO:
        shares = {
            cid: (h / total_hours * _HUNDRED).quantize(Decimal("0.01"))
            for cid, h in company_hours.items()
        }
        return shares, company_hours

    # Нет часов вообще → всё на основную компанию.
    if main_company is not None:
        return {main_company.id: _HUNDRED}, {main_company.id: _HUNDRED}
    return {}, {}


def _fmt_amount(value: Decimal | None) -> str:
    """Сумма для текста обоснования: целые рубли без хвоста, если он нулевой."""
    if value is None:
        return "0"
    value = Decimal(value)
    return str(value.quantize(Decimal("1")) if value == value.to_integral_value() else value)


def _reason_lines(items: list[tuple[Decimal, str]] | None) -> list[str]:
    """[(сумма, обоснование)] → ['5000 ₽ — за переработку', ...].

    За месяц у сотрудника может быть несколько премий/KPI/авансов, поэтому в
    отчёт идёт сумма каждой записи вместе с её текстом: одна строка «Премия
    12000» без разбивки не объясняет, откуда взялась цифра.
    """
    if not items:
        return []
    return [
        f"{_fmt_amount(amount)} ₽ — {reason}" if reason else f"{_fmt_amount(amount)} ₽"
        for amount, reason in items
    ]


# ── Распределение по количественному показателю для ТАБЕЛЯ ────────────────────

def build_quantity_distribution(
    db: Session,
    summary: PayrollSummaryRead,
    positions_by_employee: dict[int, list[EmployeePosition]],
    year: int,
    month: int,
    company_order: dict[int, int] | None = None,
) -> list[QuantityDistributionRow]:
    """Суммы распределения по юрлицам для строк табеля отдела, делящегося по
    количественному показателю (заявки у HR, АРМ у ИТ).

    Считается поверх УЖЕ посчитанного расчёта (`build_payroll_summary`) теми же
    `distribution_base` и `finalize_distribution`, что и ведомость — цифры в
    табеле и в /statement обязаны совпадать, а не «примерно сходиться».

    В выдачу попадают только рабочие места отделов с флагом И только там, где
    показатель за месяц задан: без него распределение идёт обычным каскадом, и
    показывать его в этом блоке было бы враньём.
    """
    position_by_id = {
        pos.id: pos for positions in positions_by_employee.values() for pos in positions
    }
    dept_ids = [pos.department_id for pos in position_by_id.values()]
    flagged, counts_by_dept = _quantity_context(db, dept_ids, year, month)
    if not flagged or not counts_by_dept:
        return []

    # Целевые премии/KPI уменьшают базу распределения и здесь тоже — иначе блок
    # в табеле разойдётся с ведомостью (а он обязан показывать те же цифры).
    emp_ids = list(positions_by_employee.keys())
    # Основная позиция берётся у САМОГО сотрудника, а не «первая из видимых»:
    # начисления с `position_id IS NULL` (заведённые до появления позиций)
    # принадлежат основной, и в чужом отделе они не должны прилипнуть к
    # подработке, которая случайно оказалась первой в видимом списке.
    primary_position_ids = {
        emp_id: (
            positions[0].employee.primary_position.id
            if positions and positions[0].employee.primary_position
            else None
        )
        for emp_id, positions in positions_by_employee.items()
    }
    targeted_funding = load_targeted_funding(
        db, emp_ids, year, month, primary_position_ids
    )
    # Карточка позиции перебивает показатель (task_card_priority) — здесь тоже:
    # блок в табеле обязан показывать те же суммы, что /statement.
    employee_shares = load_employee_shares(db, emp_ids, primary_position_ids, year, month)

    rows: list[QuantityDistributionRow] = []
    for p in summary.employees:
        position = position_by_id.get(p.position_id)
        if position is None:
            continue
        counts = counts_by_dept.get(position.department_id) or {}
        if not counts:
            continue
        base = distribution_base(p)
        targeted = build_targeted_funding(
            targeted_funding.get(p.employee_id, {}).get(p.position_id), base, {}
        )
        _, weights, _ = resolve_quantity_shares(
            p.position_id, counts, employee_shares
        )
        amounts = finalize_distribution(base, weights, targeted, company_order)
        rows.append(QuantityDistributionRow(
            employee_id=p.employee_id,
            position_id=p.position_id,
            department_id=position.department_id,
            base_amount=base,
            amounts=amounts,
            unallocated_remainder=unallocated_remainder(base, amounts),
        ))
    return rows


# ── Сводная ведомость ─────────────────────────────────────────────────────────

# Организация в шапке, когда ведомость собрана не по одному отделу (а значит,
# однозначного юрлица у неё нет).
ORGANIZATION_FALLBACK = "ДЕВЕЛОПМЕНТ ГРУППА «ЗЕМЛЯ МО»"
SUBDIVISION_ALL = "Все подразделения"


def _statement_heading(db: Session, department_id: DepartmentFilter) -> tuple[str, str]:
    """Организация и подразделение для шапки выгрузки (task_vedomost_format ч.3).

    Выгрузка по одному отделу подписывается его ГОЛОВНОЙ компанией — тем самым
    ярлыком дерева оргструктуры, что и в «Оргструктуре». На расчёт это не
    влияет: компании начислений берутся из часов и процентов распределения.
    Выгрузка по всем отделам (или отдел без головной компании) подписывается
    группой — однозначного юрлица у неё нет.
    """
    if department_id is NO_DEPARTMENT:
        return ORGANIZATION_FALLBACK, NO_DEPARTMENT_LABEL
    if department_id is None:
        return ORGANIZATION_FALLBACK, SUBDIVISION_ALL
    dept = db.get(Department, department_id)
    if dept is None:
        return ORGANIZATION_FALLBACK, SUBDIVISION_ALL
    company = db.get(Company, dept.head_company_id) if dept.head_company_id else None
    return (company.name if company else ORGANIZATION_FALLBACK), dept.name



def build_payroll_statement(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee | None = None,
    department_id: DepartmentFilter = None,
) -> PayrollStatementRead:
    return build_payroll_statement_with_summary(
        db, employees, entries, year, month, actor, department_id
    )[1]


def build_payroll_statement_with_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee | None = None,
    department_id: DepartmentFilter = None,
) -> tuple[PayrollSummaryRead, PayrollStatementRead]:
    """Ведомость вместе с расчётом, из которого она собрана (снимку закрытого
    периода нужны оба — `services/period_snapshots`).

    Строки позиций, лежащих в снимке закрытого периода, берутся из снимка
    целиком, с распределением по юрлицам: изменившиеся проценты, дефолт отдела
    или показатель закрытый месяц не двигают (task_stage3_historicity)."""
    snaps = load_month_snapshots(db, year, month, scope_departments(actor, department_id))
    summary = build_payroll_summary(
        db, employees, entries, year, month, actor, department_id, snapshots=snaps,
    )
    # Каскад, показатели, обоснования и вахта грузятся только для людей с живыми
    # строками: закрытый месяц целиком берётся из снимка без пересчёта.
    live_emp_ids = {
        p.employee_id for p in summary.employees if p.position_id not in snaps.statement
    }
    employees = [e for e in employees if e.id in live_emp_ids]
    emp_by_id = {e.id: e for e in employees}
    emp_ids = [e.id for e in employees]
    # Позиция строки — из неё берутся отдел, основная компания и её проценты.
    position_by_id = {
        pos.id: pos for emp in employees for pos in emp.positions
    }
    primary_position_ids = {
        emp.id: (emp.primary_position.id if emp.primary_position else None)
        for emp in employees
    }

    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(*company_order_by()).all()
    )
    # Порядок юрлиц (ч.1) — один на всю ведомость: колонки, строки разбивки
    # и итоги обязаны идти одинаково.
    company_order = order_index(c.id for c in companies)
    company_refs = [
        StatementCompanyRef(
            id=c.id, code=c.code, name=c.name,
            display_name=company_display_name(c), sort_order=c.sort_order,
        )
        for c in companies
    ]

    # Каскад приоритетов: месячный % > карточка (позиция) > отдел > авто по часам.
    employee_shares = load_employee_shares(db, emp_ids, primary_position_ids, year, month)
    override_shares = load_month_overrides(
        db, emp_ids, year, month, primary_position_ids
    )
    dept_shares = load_department_shares(
        db, [pos.department_id for pos in position_by_id.values() if pos.department_id]
    )
    # Количественный показатель отдела (заявки у HR, АРМ у ИТ): для отделов с
    # флагом он ЗАМЕНЯЕТ каскад целиком. Загружается один раз на всю ведомость —
    # набор общий для всех сотрудников отдела.
    quantity_departments, quantity_by_dept = _quantity_context(
        db, [pos.department_id for pos in position_by_id.values()], year, month
    )
    quantity_metric_names = {
        d.id: d.quantity_metric_label
        for d in db.query(Department).filter(
            Department.id.in_(quantity_departments or [0])
        ).all()
    }

    # Обоснования премий/KPI/аванса — только для отчётности, в расчёт не входят.
    adjustment_reasons = load_adjustment_reasons(
        db, emp_ids, year, month, primary_position_ids
    )
    # Премии и KPI с указанным источником финансирования (task_funding_source):
    # они относятся на своё юрлицо целиком, а каскад делит ОСТАТОК начисления.
    targeted_funding = load_targeted_funding(
        db, emp_ids, year, month, primary_position_ids
    )
    company_names = {c.id: company_display_name(c) for c in companies}

    # Вахта: проценты берутся от ПОСТА, а не от каскада. Загружаем те же строки,
    # которыми посчитан summary, — нужны их доли по юрлицам.
    guard_rows = load_guard_rows(db, list(position_by_id), year, month)

    rows: list[StatementRow] = []
    distribution_totals: dict[int, Decimal] = {c.id: _ZERO for c in companies}

    for p in summary.employees:
        if p.position_id in snaps.statement:
            snap_row = StatementRow.model_validate(snaps.statement[p.position_id])
            rows.append(snap_row)
            for item in snap_row.distribution:
                distribution_totals[item.company_id] = (
                    distribution_totals.get(item.company_id, _ZERO) + item.amount
                )
            continue
        emp = emp_by_id.get(p.employee_id)
        # Отдел, основная компания и проценты — у ПОЗИЦИИ строки, а не у человека.
        position = position_by_id.get(p.position_id)
        # «Начислено, оклад» ведомости — ТОЛЬКО оплата обычных часов
        # (task_overtime_columns). Работа в свой выходной и в праздник оплачена
        # по коэффициенту, это переработка, а не оклад: обе категории уехали в
        # колонки переработки ниже. Отдельных колонок под них в форме финдира
        # нет, а «Итого начислено» (и, значит, база распределения по юрлицам)
        # от перекладки не меняется — слагаемые те же.
        base_salary = p.base_amount
        # «Кол-во переработки» и «Сумма ПЕРЕРАБОТКи» — ОДНИ И ТЕ ЖЕ категории:
        # часы, из которых посчитана сумма рядом. Раньше в часах стояла
        # переработка, а в сумме — только она же, при этом оплата выходных
        # пряталась в окладе; теперь обе колонки собирают сверхурочные плюс
        # выходные/праздничные по графику.
        # Дельта «факт − норма» (`p.delta_hours`) в ведомость не идёт вовсе:
        # она схлопывает переработку с недоработкой и в деньгах не участвует.
        overtime_hours = (
            p.overtime_hours + p.off_schedule_hours + p.holiday_hours
        )
        overtime_amount = (
            p.overtime_amount + p.off_schedule_amount + p.holiday_amount
        )
        # Отпускные/больничные и надбавка за ночные — часть «Итого начислено» и,
        # значит, базы распределения по юрлицам (ни отсутствие, ни ночная смена
        # к юрлицу не привязаны — их разносит каскад процентов).
        accrued = accrued_total(p)
        # БАЗА распределения — «Итого начислено»: распределение отражает затраты
        # компании, а они возникают при начислении. Удержания (займ, аванс) их не
        # уменьшают, округление «К выплате» на них не влияет.
        base = distribution_base(p)
        main_company = position.company if position else None

        position_dept_id = position.department_id if position else None
        # Отдел с флагом «распределение по количественному показателю» делится по
        # нему, а не по каскаду: проценты одни на весь отдел. Показатель за месяц
        # не заведён — предупреждаем и уходим на обычный каскад, иначе правка
        # флага молча обнулила бы распределение отдела.
        qty_counts = quantity_by_dept.get(position_dept_id) or {}
        distribution_note = None
        quantity_metric_name = None

        # Целевые премии/KPI (task_funding_source) вычитаются из базы ДО каскада:
        # добавить их сверху распределённой базы значило бы посчитать эти деньги
        # дважды, и ведомость перестала бы сходиться.
        targeted = build_targeted_funding(
            targeted_funding.get(p.employee_id, {}).get(p.position_id),
            base,
            company_names,
        )

        guard_row = guard_rows.get(p.position_id) if p.is_guard_row else None
        if guard_row is not None:
            # ВАХТА. Каскад (месяц → карточка → отдел → часы) и количественный
            # показатель к этим строкам не применяются вовсе: платит объект, и
            # проценты у него свои. Суммы НЕ округляются до тысячи — считаются до
            # копейки, поэтому нераспределённого остатка у строки вахты нет.
            is_overridden = False
            is_auto = False
            shares = dict(guard_row.shares)
            weights = shares
            source = SOURCE_GUARD_POST
        elif qty_counts:
            is_overridden = False
            is_auto = False
            # Карточка ПОЗИЦИИ перебивает показатель целиком (task_card_priority);
            # иначе — веса САМИ количества, а не округлённые проценты: доля
            # компании = база × количество / Σколичеств точно (57000 × 45/104,
            # а не × 43.27%).
            shares, weights, source = resolve_quantity_shares(
                p.position_id, qty_counts, employee_shares
            )
            # Правка процентов в ведомости для таких отделов заблокирована —
            # и у строк, ушедших на карточку, тоже: исключения задаются только
            # в карточке. Подпись показателя фронт берёт отсюда.
            quantity_metric_name = quantity_metric_names.get(position_dept_id)
        else:
            shares, source = resolve_shares(
                position_id=p.position_id,
                department_id=position_dept_id,
                month_overrides=override_shares,
                employee_shares=employee_shares,
                department_shares=dept_shares,
            )
            is_overridden = source == SOURCE_MONTH
            is_auto = source == SOURCE_HOURS

            if position_dept_id in quantity_departments:
                metric = quantity_metric_names.get(position_dept_id, "Количество")
                distribution_note = (
                    f"Показатель «{metric}» за месяц не задан — "
                    "распределение по обычному каскаду"
                )

            if not is_auto:
                weights = shares
            else:
                # Ни на одном уровне каскада % не задан → авто по фактическим часам.
                shares, weights = _auto_shares_by_hours(
                    p.breakdown_by_company, main_company,
                )
        percent_sum = sum(shares.values(), _ZERO)

        # Каскад делит базу без целевых, целевые ложатся на свои юрлица, и весь
        # набор округляется ВНИЗ до ТЫСЯЧИ (ч.3): Σ долей на 0…999 ₽ меньше
        # начисленного, разница — нераспределённый остаток строки.
        if guard_row is not None:
            # Единственное место, где распределение идёт НЕ через
            # finalize_distribution: там floor до тысячи, а в вахте округления
            # нет. 75 230 по 35/60/5 обязано дать 26 330,50 / 45 138 / 3 761,50.
            dist_amounts = distribute_guard_amount(base, weights)
        else:
            dist_amounts = finalize_distribution(base, weights, targeted, company_order)
        row_unallocated = unallocated_remainder(base, dist_amounts)
        # Пометка о целевых суммах живёт в СВОЁМ поле (`targeted_note`):
        # `distribution_note` — про предупреждения каскада, и подпись
        # «⚠ показатель не задан» на экране относится именно к нему.
        if targeted.exceeds_accrued:
            distribution_note = (
                (distribution_note + "; " if distribution_note else "")
                + "целевые начисления превышают «Итого начислено» — "
                "распределены только они, пропорционально начислению"
            )

        # Компания-источник попадает в разбивку, даже если в обычном
        # распределении сотрудника её не было (у неё нет процента каскада — 0).
        distribution_ids = sorted(
            set(shares) | set(targeted.amounts),
            key=lambda c: (company_order.get(c, len(company_order)), c),
        )
        distribution = [
            StatementCompanyAmount(
                company_id=cid,
                percent=shares.get(cid, _ZERO),
                # Фактическая доля юрлица в начислении: с целевыми суммами она
                # отличается от заданного в каскаде процента (40/60 при 50/50).
                effective_percent=effective_percent(
                    dist_amounts.get(cid, _ZERO), base
                ),
                amount=dist_amounts.get(cid, _ZERO),
            )
            for cid in distribution_ids
        ]
        for cid, amt in dist_amounts.items():
            distribution_totals[cid] = distribution_totals.get(cid, _ZERO) + amt

        overtime_coeff = (
            getattr(terms_on(position, month_bounds(year, month)[1]), "overtime_coefficient", None)
            if position else None
        )
        overtime_coeff = Decimal("1.5") if overtime_coeff is None else Decimal(str(overtime_coeff))

        reasons = adjustment_reasons.get(p.employee_id, {}).get(p.position_id, {})
        loan_note = (
            f"займ: удержание изменено вручную (плановая доля "
            f"{_fmt_amount(p.loan_planned_deduction)} ₽)"
            if p.loan_is_manual else None
        )
        if p.loan_shortfall > _ZERO:
            # п.5.1: начисления не хватило — удержано меньше плана, долг и срок
            # займа сдвигаются на следующие месяцы.
            loan_note = (
                f"займ: начисления не хватило — удержано "
                f"{_fmt_amount(p.loan_deduction)} ₽ из {_fmt_amount(p.loan_planned_deduction)} ₽, "
                f"остаток переносится"
            )

        rows.append(StatementRow(
            employee_id=p.employee_id,
            position_id=p.position_id,
            is_primary_position=p.is_primary_position,
            tab_number=emp.tab_number if emp else None,
            employee_name=p.employee_name,
            main_company_id=main_company.id if main_company else None,
            main_company_name=main_company.name if main_company else None,
            department_name=(
                position.department.name if position and position.department else None
            ),
            department_id=(position.department_id if position else None),
            department_head_company_id=(
                position.department.head_company_id
                if position and position.department else None
            ),
            # Должность — название рабочего места; у совместителя строки
            # различаются именно им.
            position=(position.display_title if position else (emp.position if emp else None)),
            schedule_name=p.schedule_name,
            rate=p.rate,
            pay_type=p.pay_type,
            shift_rate=p.shift_rate,
            hour_rate=p.hour_rate,
            worked_shifts=p.worked_shifts,
            norm_shifts=p.norm_shifts,
            base_shifts=p.base_shifts,
            norm_hours=p.norm_hours,
            fact_hours=p.total_hours,
            norm_days=p.norm_days,
            fact_days=p.fact_days,
            overtime_coefficient=overtime_coeff,
            overtime_hours=overtime_hours,
            overtime_amount=overtime_amount,
            base_salary=base_salary,
            premium_amount=p.premium_amount,
            kpi_amount=p.kpi_amount,
            premium_extra_amount=_ZERO,
            premium_reasons=_reason_lines(reasons.get("premium")),
            kpi_reasons=_reason_lines(reasons.get("kpi")),
            advance_reasons=_reason_lines(reasons.get("advance")),
            loan_note=loan_note,
            vacation_days=p.vacation_days,
            sick_days=p.sick_days,
            unpaid_days=p.unpaid_days,
            absent_days=p.absent_days,
            vacation_amount=p.vacation_amount,
            sick_amount=p.sick_amount,
            sick_limit_days=p.sick_limit_days,
            sick_unpaid_days=p.sick_unpaid_days,
            sick_limit_remaining=p.sick_limit_remaining,
            night_shifts=p.night_shifts,
            night_rate=p.night_rate,
            night_amount=p.night_amount,
            accrued_total=accrued,
            deductions=p.total_deductions,
            net_payout=p.net_payout,
            net_payout_exact=p.net_payout_exact,
            rounding_tail=p.rounding_tail,
            is_overridden=is_overridden,
            is_auto_distributed=is_auto,
            distribution_source=source,
            quantity_metric_name=quantity_metric_name,
            distribution_note=distribution_note,
            targeted_amounts=targeted.amounts,
            targeted_total=targeted.total,
            targeted_note=targeted.note,
            percent_sum=percent_sum,
            distribution=distribution,
            distribution_total=sum(dist_amounts.values(), _ZERO),
            unallocated_remainder=row_unallocated,
            guard_tax_amount=p.guard_tax_amount,
            is_calculable=p.is_calculable,
            note=p.reason_if_not_calculable,
        ))

    organization, subdivision = _statement_heading(db, department_id)
    return summary, PayrollStatementRead(
        year=year,
        month=month,
        organization=organization,
        subdivision=subdivision,
        companies=company_refs,
        rows=rows,
        total_overtime_amount=sum((r.overtime_amount for r in rows), _ZERO),
        total_base_salary=sum((r.base_salary for r in rows), _ZERO),
        total_vacation_amount=sum((r.vacation_amount for r in rows), _ZERO),
        total_sick_amount=sum((r.sick_amount for r in rows), _ZERO),
        total_night_amount=sum((r.night_amount for r in rows), _ZERO),
        total_premium=sum((r.premium_amount for r in rows), _ZERO),
        total_kpi=sum((r.kpi_amount for r in rows), _ZERO),
        total_accrued=sum((r.accrued_total for r in rows), _ZERO),
        total_deductions=sum((r.deductions for r in rows), _ZERO),
        total_net_payout=sum((r.net_payout for r in rows), _ZERO),
        total_net_payout_exact=sum((r.net_payout_exact for r in rows), _ZERO),
        total_rounding_tail=sum((r.rounding_tail for r in rows), _ZERO),
        total_unallocated_remainder=sum(
            (r.unallocated_remainder for r in rows), _ZERO
        ),
        distribution_totals=distribution_totals,
    )
