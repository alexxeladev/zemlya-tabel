"""Агрегация данных для дашборда (задача 4.1).

Переиспользует расчёт ЗП (calculate_employee_payroll) и видимость по ролям
(visible_employees_for_actor) — дашборд обязан показывать те же цифры,
что табель и страница расчёта. Формулы здесь НЕ дублируются.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.dashboard import (
    CompanyPayrollRead,
    DashboardResponse,
    DepartmentHoursRead,
    DepartmentPayrollRead,
    HoursSummaryRead,
    PayrollTotalsRead,
    PeriodCountsRead,
    PeriodsBlockRead,
    PeriodStatusRowRead,
    TrendPointRead,
)
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
from app.services.night_shifts import load_night_context
from app.services.org_access import (
    can_see_finances,
    is_department_scoped,
    managed_department_ids,
)
from app.services.payroll import EmployeePayroll, calculate_position_payroll
from app.services.payroll_statement import build_payroll_statement
from app.services.positions import (
    department_employment_rows,
    departments_with_employees,
    entries_by_position,
    visible_positions,
)
from app.services.timesheet import get_month_entries, visible_employees_for_actor

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_PERCENT_Q = Decimal("0.1")

TREND_MONTHS = 6

# Потолок длины диапазона (task_ux_improvements ч.2). Каждый месяц — полный
# расчёт ЗП всех видимых сотрудников, то есть цена запроса линейна по месяцам;
# год — разумный максимум, который и просили (квартал/год/произвольно).
MAX_RANGE_MONTHS = 12

# (employee, payroll-результат) за один месяц
# Строка результата — (сотрудник, ПОЗИЦИЯ, расчёт): у совместителя человек
# встречается столько раз, сколько у него рабочих мест (task_positions ч.A).
_MonthResults = list[tuple[Employee, Optional[EmployeePosition], EmployeePayroll]]


# ── Помесячный расчёт (reuse payroll) ─────────────────────────────────────────

def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _last_n_months(year: int, month: int, n: int) -> list[tuple[int, int]]:
    """n месяцев по возрастанию, последний — (year, month)."""
    out = [(year, month)]
    for _ in range(n - 1):
        year, month = _prev_month(year, month)
        out.append((year, month))
    return list(reversed(out))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def months_in_range(
    year: int, month: int, to_year: int, to_month: int
) -> list[tuple[int, int]]:
    """Месяцы диапазона включительно, по возрастанию.

    Каждый месяц диапазона считается отдельно (ЗП помесячная — нормы, лимит
    больничного и статусы периодов месячные), поэтому длина диапазона напрямую
    определяет стоимость запроса: см. `MAX_RANGE_MONTHS`.
    """
    out: list[tuple[int, int]] = []
    y, m = year, month
    while (y, m) <= (to_year, to_month):
        out.append((y, m))
        y, m = _next_month(y, m)
    return out


def _month_payrolls(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]],
    calendars_cache: dict[int, dict | None],
) -> _MonthResults:
    """Расчёт ЗП всех видимых сотрудников за месяц — тот же путь, что в табеле."""
    if year not in calendars_cache:
        cal = db.query(ProductionCalendar).filter_by(year=year).first()
        calendars_cache[year] = cal.data if cal else None
    calendar_data = calendars_cache[year]

    employees = visible_employees_for_actor(db, actor, None, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    by_emp: dict[int, list] = {}
    for e in entries:
        by_emp.setdefault(e.employee_id, []).append(e)

    # Отсутствия — тоже часть ФОТ (отпускные/больничные), иначе дашборд
    # разойдётся с /payroll у сотрудников с ОТ/Б.
    absences_by_emp: dict[int, list] = {}
    for a in get_month_absences(db, employees, year, month):
        absences_by_emp.setdefault(a.employee_id, []).append(a)
    sick_used_before = sick_days_used_before_month(
        db, [e.id for e in employees], year, month, calendar_data,
        schedules_by_employee(employees),
    )
    # Ночные — часть ФОТ (надбавка входит в total_amount), иначе дашборд
    # разошёлся бы с /payroll у отделов, где ночные отмечены.
    night = load_night_context(db, employees, year, month)

    # По одной строке на ПОЗИЦИЮ (task_positions ч.A) — так же, как считает
    # /payroll: иначе у совместителя ФОТ дашборда разошёлся бы с табелем.
    results: _MonthResults = []
    for emp in employees:
        by_position = entries_by_position(emp, by_emp.get(emp.id, []))
        for position in visible_positions(emp, actor) or [emp.primary_position]:
            results.append((
                emp,
                position,
                calculate_position_payroll(
                    emp, position,
                    by_position.get(position.id, []) if position else [],
                    calendar_data, year, month, companies_by_id,
                    absences=absences_by_emp.get(emp.id, []),
                    sick_days_used_before=sick_used_before.get(emp.id, 0),
                    night_shifts=night.shifts_of(position),
                    night_rate=night.rate_of(position),
                ),
            ))
    return results


# ── Блок 1: часы ──────────────────────────────────────────────────────────────

def _sum_hours(results: _MonthResults) -> tuple[Decimal, Decimal | None, Decimal]:
    """(отработано, норма | None, переработка) по списку результатов."""
    total = sum((p.total_hours for *_, p in results), _ZERO)
    overtime = sum((p.overtime_hours for *_, p in results), _ZERO)
    norms = [p.norm_hours for *_, p in results if p.norm_hours is not None]
    norm = sum(norms, _ZERO) if norms else None
    return total, norm, overtime


def _hours_summary(results: _MonthResults) -> HoursSummaryRead:
    total, norm, overtime = _sum_hours(results)
    percent = None
    if norm is not None and norm > _ZERO:
        percent = (total / norm * _HUNDRED).quantize(_PERCENT_Q, rounding=ROUND_HALF_EVEN)
    return HoursSummaryRead(
        total_hours=total, norm_hours=norm, overtime_hours=overtime, percent_of_norm=percent
    )


def _group_by_department(
    db: Session, results: _MonthResults
) -> list[tuple[int | None, str, _MonthResults]]:
    by_dept: dict[int | None, _MonthResults] = {}
    for emp, position, p in results:
        # Группируем по отделу ПОЗИЦИИ: подработка в другом отделе и должна
        # попасть в тот отдел, а не в отдел основного места работы.
        dept_id = position.department_id if position is not None else None
        by_dept.setdefault(dept_id, []).append((emp, position, p))

    dept_ids = [d for d in by_dept if d is not None]
    names: dict[int, str] = {}
    if dept_ids:
        for d in db.query(Department).filter(Department.id.in_(dept_ids)).all():
            names[d.id] = d.name

    groups = [
        (dept_id, names.get(dept_id, "Без отдела") if dept_id is not None else "Без отдела", items)
        for dept_id, items in by_dept.items()
    ]
    # Сортировка по имени, «Без отдела» — в конец
    groups.sort(key=lambda g: (g[0] is None, g[1]))
    return groups


def _hours_by_department(db: Session, results: _MonthResults) -> list[DepartmentHoursRead]:
    out = []
    for dept_id, name, items in _group_by_department(db, results):
        total, norm, overtime = _sum_hours(items)
        out.append(DepartmentHoursRead(
            department_id=dept_id, department_name=name,
            total_hours=total, norm_hours=norm, overtime_hours=overtime,
        ))
    return out


# ── Блок 2: ФОТ ───────────────────────────────────────────────────────────────

def _payroll_totals(
    results: _MonthResults, rounding_effect: Decimal, unallocated: Decimal
) -> PayrollTotalsRead:
    # Не вошедшие в расчёт считаются по РАЗНЫМ рабочим местам, а не по строкам:
    # в диапазоне месяцев один и тот же сотрудник без графика встречается
    # столько раз, сколько месяцев выбрано, и счётчик бы врал.
    non_calculable = {
        (emp.id, position.id if position is not None else None)
        for emp, position, p in results
        if not p.is_calculable
    }
    return PayrollTotalsRead(
        total=sum((p.total_amount for *_, p in results), _ZERO),
        base=sum((p.base_amount for *_, p in results), _ZERO),
        overtime=sum((p.overtime_amount for *_, p in results), _ZERO),
        off_schedule=sum((p.off_schedule_amount for *_, p in results), _ZERO),
        holiday=sum((p.holiday_amount for *_, p in results), _ZERO),
        rounding_effect=rounding_effect,
        unallocated_remainder=unallocated,
        non_calculable_employees=len(non_calculable),
    )


def _rounding_and_unallocated(
    db: Session, actor: Employee, year: int, month: int
) -> tuple[Decimal, Decimal]:
    """Два РАЗНЫХ показателя округления за месяц, одним проходом.

    1. «Эффект округления» — Σ хвостов округления «к выплате» до 1000 ₽. Знак
       ЛЮБОЙ и по строкам, и в сумме: вниз оседает в пользу компании (+), вверх
       компания доплачивает (−). Отрицательный итог за период нормален.
    2. «Нераспределённый остаток» — Σ (Итого начислено − Σ распределения по
       юрлицам). Суммы по юрлицам округляются ВНИЗ до 1000 ₽, поэтому по строке
       остаётся 0…999 ₽; никому не приписывается. Всегда ≥ 0.

    Смешивать их нельзя: первый про выплату, второй про затраты юрлиц.

    Считается через `build_payroll_statement` — тот же путь, что у ведомости
    (премии/KPI/удержания и распределение в ФОТ дашборда не входят, поэтому из
    результатов _month_payrolls этих чисел не получить). Видимость по ролям —
    через `visible_employees_for_actor` (manager видит только свой отдел).
    """
    employees = visible_employees_for_actor(db, actor, None, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    statement = build_payroll_statement(db, employees, entries, year, month, actor)
    return statement.total_rounding_tail, statement.total_unallocated_remainder


def _payroll_by_department(db: Session, results: _MonthResults) -> list[DepartmentPayrollRead]:
    out = []
    for dept_id, name, items in _group_by_department(db, results):
        out.append(DepartmentPayrollRead(
            department_id=dept_id, department_name=name,
            total=sum((p.total_amount for *_, p in items), _ZERO),
        ))
    return out


def _payroll_by_company(
    results: _MonthResults,
    companies_by_id: dict[int, tuple[str, str]],
    companies: list[Company],
) -> list[CompanyPayrollRead]:
    totals: dict[int, Decimal] = {}
    for *_, p in results:
        for bd in p.breakdown_by_company:
            totals[bd.company_id] = totals.get(bd.company_id, _ZERO) + bd.total
    out = []
    # Порядок юрлиц — настроенный в справочнике: companies_by_id собран из
    # запроса с company_order_by(), то есть его ключи уже идут как надо.
    index = order_index(companies_by_id)
    display = {c.id: company_display_name(c) for c in companies}
    for cid in sorted(totals, key=lambda c: (index.get(c, len(index)), c)):
        code, name = companies_by_id.get(cid, ("", f"Компания #{cid}"))
        out.append(CompanyPayrollRead(
            company_id=cid, company_code=code, company_name=name,
            company_display_name=display.get(cid, name), total=totals[cid],
        ))
    return out


# ── Блок 3: статусы периодов ──────────────────────────────────────────────────

def _period_row(
    period: TimesheetPeriod | None,
    dept_id: int | None,
    dept_name: str,
    year: int,
    month: int,
    is_overdue: bool = False,
) -> PeriodStatusRowRead:
    return PeriodStatusRowRead(
        period_id=period.id if period else None,
        department_id=dept_id,
        department_name=dept_name,
        year=year,
        month=month,
        status=period.status if period else "draft",  # lazy-период ещё не создан
        submitted_by_name=(
            period.submitted_by.full_name if period and period.submitted_by else None
        ),
        closed_by_name=(
            period.closed_by.full_name if period and period.closed_by else None
        ),
        is_overdue=is_overdue,
    )


def _periods_block(
    db: Session,
    actor: Employee,
    months: list[tuple[int, int]],
) -> PeriodsBlockRead:
    """Статусы периодов за все месяцы диапазона.

    Строка — (отдел, месяц): у периода статус месячный, «свернуть» диапазон в
    одну строку на отдел значило бы потерять, какой именно месяц не закрыт.
    Просрочка считается от НАЧАЛА диапазона: незакрытый период более раннего
    месяца — просрочен, месяцы внутри диапазона показаны своими строками.

    **Отдел без сотрудников в workflow не участвует ВООБЩЕ** — ни строкой, ни
    просрочкой, ни в счётчиках: закрывать там нечего, а «Черновик · просрочен» у
    пустого отдела — чистый шум. Раньше список строился из `departments` и
    `timesheet_periods`, и есть ли в отделе люди, никто не спрашивал: отдел,
    опустевший после увольнений или переводов, навсегда оставался просроченным,
    а псевдо-отдел «Без отдела» проверялся только в строках текущего месяца и
    всё равно вылезал в просрочке. Занятость своя у каждого месяца (увольнения),
    поэтому считается по месяцам из одного снимка.
    """
    year, month = months[0]
    employment = department_employment_rows(db)
    staffed_cache: dict[tuple[int, int], set[int | None]] = {}

    def staffed(y: int, m: int) -> set[int | None]:
        if (y, m) not in staffed_cache:
            staffed_cache[(y, m)] = departments_with_employees(employment, y, m)
        return staffed_cache[(y, m)]

    # Отделы в зоне видимости actor-а
    if is_department_scoped(actor):
        # Все отделы, которыми руководит менеджер (task_org_structure ч.2) или
        # которые ведёт табельщик (task_timekeeper_role)
        managed = managed_department_ids(actor)
        depts = (
            db.query(Department).filter(Department.id.in_(managed)).all() if managed else []
        )
        # Группа «Без отдела» менеджеру и табельщику недоступна никогда
        include_null_group = False
    else:
        depts = db.query(Department).filter(Department.is_active == True).all()  # noqa: E712
        include_null_group = True

    periods = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.year.in_({y for y, _ in months}),
    ).all()
    by_key = {(p.year, p.month, p.department_id): p for p in periods}

    rows: list[PeriodStatusRowRead] = []
    for y, m in months:
        present = staffed(y, m)
        rows.extend(
            _period_row(by_key.get((y, m, d.id)), d.id, d.name, y, m)
            for d in sorted(depts, key=lambda d: d.name)
            if d.id in present
        )
        # Псевдо-отдел «Без отдела» — только когда в нём реально кто-то есть
        if include_null_group and None in present:
            rows.append(_period_row(by_key.get((y, m, None)), None, "Без отдела", y, m))

    # Просроченные: незакрытые периоды месяцев раньше выбранного
    overdue_q = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.status != "closed",
        or_(
            TimesheetPeriod.year < year,
            (TimesheetPeriod.year == year) & (TimesheetPeriod.month < month),
        ),
    )
    if is_department_scoped(actor):
        overdue_q = overdue_q.filter(
            TimesheetPeriod.department_id.in_(managed_department_ids(actor))
        )
    overdue_periods = overdue_q.order_by(
        TimesheetPeriod.year.desc(), TimesheetPeriod.month.desc()
    ).all()
    overdue_rows = [
        _period_row(
            p, p.department_id,
            p.department.name if p.department else "Без отдела",
            p.year, p.month, is_overdue=True,
        )
        for p in overdue_periods
        # Период остался от отдела, где сотрудников уже (или ещё) нет —
        # закрывать в нём нечего, просрочкой это не является
        if p.department_id in staffed(p.year, p.month)
    ]

    counts = PeriodCountsRead(
        # «Всего отделов» — РАЗНЫЕ отделы с сотрудниками за выбранный период
        # (в диапазоне у отдела строка на каждый месяц, но отдел один). Для
        # одного месяца это ровно closed + pending_review + draft.
        departments=len({r.department_id for r in rows}),
        closed=sum(1 for r in rows if r.status == "closed"),
        pending_review=sum(1 for r in rows if r.status == "pending_review"),
        draft=sum(1 for r in rows if r.status == "draft"),
        overdue=len(overdue_rows),
    )
    return PeriodsBlockRead(counts=counts, rows=rows, overdue_rows=overdue_rows)


# ── Блок 4: динамика ──────────────────────────────────────────────────────────

def _trend(
    db: Session,
    actor: Employee,
    trend_months: list[tuple[int, int]],
    companies_by_id: dict[int, tuple[str, str]],
    calendars_cache: dict[int, dict | None],
    include_money: bool,
    computed: dict[tuple[int, int], _MonthResults],
) -> list[TrendPointRead]:
    """Динамика по месяцам.

    `computed` — уже посчитанные месяцы (месяцы выбранного диапазона): второй
    раз их считать незачем, расчёт месяца — самая дорогая часть дашборда.
    """
    points = []
    for y, m in trend_months:
        results = (
            computed[(y, m)]
            if (y, m) in computed
            else _month_payrolls(db, actor, y, m, companies_by_id, calendars_cache)
        )
        total, _, overtime = _sum_hours(results)
        payroll_total = (
            sum((p.total_amount for *_, p in results), _ZERO) if include_money else None
        )
        points.append(TrendPointRead(
            year=y, month=m,
            total_hours=total, overtime_hours=overtime, payroll_total=payroll_total,
        ))

    # Пустые месяцы в начале истории не несут информации — обрезаем,
    # но выбранный месяц оставляем всегда.
    while len(points) > 1 and points[0].total_hours == _ZERO:
        points.pop(0)
    return points


# ── Сборка ответа ─────────────────────────────────────────────────────────────

def build_dashboard(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    to_year: int | None = None,
    to_month: int | None = None,
) -> DashboardResponse:
    """Дашборд за месяц или за ДИАПАЗОН месяцев (task_ux_improvements ч.2).

    (year, month) — начало диапазона, (to_year, to_month) — конец включительно;
    не заданы — диапазон из одного месяца, поведение как раньше.

    Все показатели суммируются по месяцам диапазона (часы, ФОТ, эффект
    округления), статусы периодов показываются построчно по месяцам, а динамика
    для диапазона строится по его же месяцам (для одиночного месяца остаётся
    хвост из TREND_MONTHS назад — иначе график выродился бы в одну точку).
    """
    to_year = year if to_year is None else to_year
    to_month = month if to_month is None else to_month

    # Финансовый блок (ФОТ) — только ролям с доступом к деньгам: табельщик видит
    # часы и статусы периодов своих отделов, ФОТ ему не отдаётся.
    include_money = can_see_finances(actor)
    is_employee = actor.role == "employee"

    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(*company_order_by()).all()
    )
    companies_by_id = {c.id: (c.code, c.name) for c in companies}
    calendars_cache: dict[int, dict | None] = {}

    months = months_in_range(year, month, to_year, to_month)
    by_month = {
        (y, m): _month_payrolls(db, actor, y, m, companies_by_id, calendars_cache)
        for y, m in months
    }
    # Показатели диапазона — по всем его месяцам разом.
    current: _MonthResults = [r for m in months for r in by_month[m]]

    rounding, unallocated = _ZERO, _ZERO
    if include_money:
        for y, m in months:
            tail, rest = _rounding_and_unallocated(db, actor, y, m)
            rounding += tail
            unallocated += rest
    trend_months = months if len(months) > 1 else _last_n_months(year, month, TREND_MONTHS)

    return DashboardResponse(
        year=year,
        month=month,
        to_year=to_year,
        to_month=to_month,
        months_count=len(months),
        role=actor.role,
        hours=_hours_summary(current),
        hours_by_department=[] if is_employee else _hours_by_department(db, current),
        payroll=(
            _payroll_totals(current, rounding, unallocated) if include_money else None
        ),
        payroll_by_department=(
            _payroll_by_department(db, current) if include_money else []
        ),
        payroll_by_company=(
            _payroll_by_company(current, companies_by_id, companies)
            if include_money else []
        ),
        periods=None if is_employee else _periods_block(db, actor, months),
        trend=_trend(
            db, actor, trend_months, companies_by_id, calendars_cache,
            include_money, by_month,
        ),
    )
