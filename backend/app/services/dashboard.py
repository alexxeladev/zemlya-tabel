"""Агрегация данных для дашборда (задача 4.1).

Переиспользует расчёт ЗП (calculate_employee_payroll) и видимость по ролям
(visible_employees_for_actor) — дашборд обязан показывать те же цифры,
что табель и страница расчёта. Формулы здесь НЕ дублируются.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
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
from app.services.payroll import EmployeePayroll, calculate_employee_payroll
from app.services.timesheet import get_month_entries, visible_employees_for_actor

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_PERCENT_Q = Decimal("0.1")

TREND_MONTHS = 6

# (employee, payroll-результат) за один месяц
_MonthResults = list[tuple[Employee, EmployeePayroll]]


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

    return [
        (
            emp,
            calculate_employee_payroll(
                emp, by_emp.get(emp.id, []), calendar_data, year, month, companies_by_id
            ),
        )
        for emp in employees
    ]


# ── Блок 1: часы ──────────────────────────────────────────────────────────────

def _sum_hours(results: _MonthResults) -> tuple[Decimal, Decimal | None, Decimal]:
    """(отработано, норма | None, переработка) по списку результатов."""
    total = sum((p.total_hours for _, p in results), _ZERO)
    overtime = sum((p.overtime_hours for _, p in results), _ZERO)
    norms = [p.norm_hours for _, p in results if p.norm_hours is not None]
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
    for emp, p in results:
        by_dept.setdefault(emp.department_id, []).append((emp, p))

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

def _payroll_totals(results: _MonthResults) -> PayrollTotalsRead:
    return PayrollTotalsRead(
        total=sum((p.total_amount for _, p in results), _ZERO),
        base=sum((p.base_amount for _, p in results), _ZERO),
        overtime=sum((p.overtime_amount for _, p in results), _ZERO),
        holiday=sum((p.holiday_amount for _, p in results), _ZERO),
        non_calculable_employees=sum(1 for _, p in results if not p.is_calculable),
    )


def _payroll_by_department(db: Session, results: _MonthResults) -> list[DepartmentPayrollRead]:
    out = []
    for dept_id, name, items in _group_by_department(db, results):
        out.append(DepartmentPayrollRead(
            department_id=dept_id, department_name=name,
            total=sum((p.total_amount for _, p in items), _ZERO),
        ))
    return out


def _payroll_by_company(
    results: _MonthResults, companies_by_id: dict[int, tuple[str, str]]
) -> list[CompanyPayrollRead]:
    totals: dict[int, Decimal] = {}
    for _, p in results:
        for bd in p.breakdown_by_company:
            totals[bd.company_id] = totals.get(bd.company_id, _ZERO) + bd.total
    out = []
    for cid in sorted(totals):
        code, name = companies_by_id.get(cid, ("", f"Компания #{cid}"))
        out.append(CompanyPayrollRead(
            company_id=cid, company_code=code, company_name=name, total=totals[cid],
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


def _periods_block(db: Session, actor: Employee, year: int, month: int) -> PeriodsBlockRead:
    # Отделы в зоне видимости actor-а
    if actor.role == "manager":
        depts = (
            db.query(Department).filter(Department.id == actor.department_id).all()
            if actor.department_id is not None
            else []
        )
        include_null_group = False
    else:
        depts = db.query(Department).filter(Department.is_active == True).all()  # noqa: E712
        # Группа «Без отдела» — если есть активные несистемные сотрудники без отдела
        include_null_group = db.query(Employee).filter(
            Employee.department_id.is_(None),
            Employee.is_system_admin == False,  # noqa: E712
            Employee.is_active == True,  # noqa: E712
        ).first() is not None

    periods = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.year == year, TimesheetPeriod.month == month
    ).all()
    by_dept = {p.department_id: p for p in periods}

    rows = [
        _period_row(by_dept.get(d.id), d.id, d.name, year, month)
        for d in sorted(depts, key=lambda d: d.name)
    ]
    if include_null_group:
        rows.append(_period_row(by_dept.get(None), None, "Без отдела", year, month))

    # Просроченные: незакрытые периоды месяцев раньше выбранного
    overdue_q = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.status != "closed",
        or_(
            TimesheetPeriod.year < year,
            (TimesheetPeriod.year == year) & (TimesheetPeriod.month < month),
        ),
    )
    if actor.role == "manager":
        overdue_q = overdue_q.filter(TimesheetPeriod.department_id == actor.department_id)
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
    ]

    counts = PeriodCountsRead(
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
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]],
    calendars_cache: dict[int, dict | None],
    include_money: bool,
    current_results: _MonthResults,
) -> list[TrendPointRead]:
    points = []
    for y, m in _last_n_months(year, month, TREND_MONTHS):
        results = (
            current_results
            if (y, m) == (year, month)
            else _month_payrolls(db, actor, y, m, companies_by_id, calendars_cache)
        )
        total, _, overtime = _sum_hours(results)
        payroll_total = (
            sum((p.total_amount for _, p in results), _ZERO) if include_money else None
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

def build_dashboard(db: Session, actor: Employee, year: int, month: int) -> DashboardResponse:
    include_money = actor.role in ("admin", "accountant", "manager")
    is_employee = actor.role == "employee"

    companies = db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    companies_by_id = {c.id: (c.code, c.name) for c in companies}
    calendars_cache: dict[int, dict | None] = {}

    current = _month_payrolls(db, actor, year, month, companies_by_id, calendars_cache)

    return DashboardResponse(
        year=year,
        month=month,
        role=actor.role,
        hours=_hours_summary(current),
        hours_by_department=[] if is_employee else _hours_by_department(db, current),
        payroll=_payroll_totals(current) if include_money else None,
        payroll_by_department=(
            _payroll_by_department(db, current) if include_money else []
        ),
        payroll_by_company=(
            _payroll_by_company(current, companies_by_id) if include_money else []
        ),
        periods=None if is_employee else _periods_block(db, actor, year, month),
        trend=_trend(
            db, actor, year, month, companies_by_id, calendars_cache,
            include_money, current,
        ),
    )
