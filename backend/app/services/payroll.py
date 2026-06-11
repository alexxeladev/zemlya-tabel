from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.calendar import (
    is_holiday,
    is_short_day,
    norm_hours_for_period,
    workdays_in_month,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_ONE_HALF = Decimal("1.5")
_HUNDRED = Decimal("100")
_PERCENT_Q = Decimal("0.1")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_ONE, rounding=ROUND_HALF_EVEN)


def _weekend_pay(employee: Employee, holiday_hours: Decimal, hourly_rate: Decimal) -> Decimal:
    """
    Оплата праздничных/выходных часов по настройкам конкретного сотрудника
    (правка 3.9-3). По умолчанию — коэффициент 1.5.
      - coefficient: holiday_hours × hourly_rate × коэффициент (0 = не оплачивается)
      - fixed_rate:  holiday_hours × фикс_ставка (не зависит от оклада)
    """
    if holiday_hours <= _ZERO:
        return _ZERO

    pay_type = getattr(employee, "weekend_pay_type", None) or "coefficient"

    if pay_type == "fixed_rate":
        fixed = getattr(employee, "weekend_fixed_rate", None)
        if fixed is None:
            return _ZERO
        return holiday_hours * Decimal(str(fixed))

    coeff = getattr(employee, "weekend_coefficient", None)
    coeff = _ONE_HALF if coeff is None else Decimal(str(coeff))
    return holiday_hours * hourly_rate * coeff


@dataclass
class CompanyBreakdown:
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    percent: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total: Decimal


@dataclass
class EmployeePayroll:
    employee_id: int
    employee_name: str
    rate: Decimal | None
    schedule_name: str | None

    total_hours: Decimal
    norm_hours: Decimal | None
    delta_hours: Decimal | None
    overtime_hours: Decimal
    holiday_hours: Decimal

    norm_days: int | None
    fact_days: int

    hourly_rate: Decimal | None

    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total_amount: Decimal

    breakdown_by_company: list[CompanyBreakdown]
    is_calculable: bool
    reason_if_not_calculable: str | None


def calculate_employee_payroll(
    employee: Employee,
    entries: list[TimesheetEntry],
    calendar_data: dict | None,
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]] | None = None,
) -> EmployeePayroll:
    """
    Чистая функция: считает зарплату сотрудника за период.
    Не лезет в БД, принимает все данные на вход.
    companies_by_id: dict[company_id → (code, name)]
    """
    if companies_by_id is None:
        companies_by_id = {}

    # Aggregate hours by company and by date; detect holiday hours per company.
    company_hours: dict[int, Decimal] = {}
    company_holiday_hours: dict[int, Decimal] = {}
    hours_by_date: dict[date, Decimal] = {}
    total_hours = _ZERO
    total_holiday_hours = _ZERO

    for entry in entries:
        cid = entry.company_id
        h = entry.hours if isinstance(entry.hours, Decimal) else Decimal(str(entry.hours))
        total_hours += h
        if cid not in company_hours:
            company_hours[cid] = _ZERO
            company_holiday_hours[cid] = _ZERO
        company_hours[cid] += h
        hours_by_date[entry.work_date] = hours_by_date.get(entry.work_date, _ZERO) + h

        if calendar_data is not None and is_holiday(
            calendar_data, entry.work_date.month, entry.work_date.day
        ):
            company_holiday_hours[cid] += h
            total_holiday_hours += h

    # Норма/факт дней (правка 3.9-4) — справочные, в деньгах не участвуют.
    # Норма дней = рабочих дней по календарю (сокращённые считаются как полный день).
    # Факт дней = дней, в которых есть хотя бы один час работы (по всем компаниям).
    norm_days: int | None = (
        workdays_in_month(calendar_data, year, month) if calendar_data is not None else None
    )
    fact_days = len(hours_by_date)

    # Determine calculability and norm
    schedule = employee.schedule
    schedule_name = schedule.name if schedule else None
    is_calculable = True
    reason: str | None = None
    norm_hours: Decimal | None = None

    if schedule is None:
        is_calculable = False
        reason = "Не задан график"
    elif schedule.schedule_type == "shift":
        is_calculable = False
        reason = "Сменный график не поддерживается"
    elif calendar_data is None:
        is_calculable = False
        reason = "Производственный календарь не загружен"
    else:
        norm_val = norm_hours_for_period(calendar_data, year, month, schedule.hours_per_shift)
        norm_hours = Decimal(str(norm_val))
        if norm_hours == _ZERO:
            is_calculable = False
            reason = "Норма не определена (0 рабочих дней)"

    rate = employee.rate
    if is_calculable and (rate is None or rate == _ZERO):
        is_calculable = False
        reason = "Не задан оклад"

    # Переработка строго по дням (правка 3.9-2): для каждого рабочего дня
    # сравниваем фактические часы с дневной нормой графика. Праздничные/выходные
    # часы — отдельная категория, в переработку и базу оклада не идут.
    delta_hours: Decimal | None = None
    overtime_hours = _ZERO
    regular_credited_hours = _ZERO
    if norm_hours is not None:
        delta_hours = total_hours - norm_hours

    if calendar_data is not None and schedule is not None and schedule.schedule_type != "shift":
        hps = Decimal(str(schedule.hours_per_shift))
        for d, day_hours in hours_by_date.items():
            if is_holiday(calendar_data, d.month, d.day):
                continue  # праздничные часы оплачиваются по правилам выходных
            daily_norm = hps - _ONE if is_short_day(calendar_data, d.month, d.day) else hps
            if day_hours > daily_norm:
                overtime_hours += day_hours - daily_norm
                regular_credited_hours += daily_norm
            else:
                regular_credited_hours += day_hours

    # Financial amounts
    hourly_rate: Decimal | None = None
    base_amount = _ZERO
    overtime_amount = _ZERO
    holiday_amount = _ZERO

    if is_calculable and rate is not None and norm_hours is not None and norm_hours > _ZERO:
        hourly_rate = rate / norm_hours
        # Оклад от зачётных будних часов, но не больше полного оклада.
        base_amount = rate * min(_ONE, regular_credited_hours / norm_hours)
        overtime_amount = overtime_hours * hourly_rate * _ONE_HALF
        # Праздничные/выходные — по персональным настройкам сотрудника (правка 3.9-3).
        holiday_amount = _weekend_pay(employee, total_holiday_hours, hourly_rate)

    base_amount = _round(base_amount)
    overtime_amount = _round(overtime_amount)
    holiday_amount = _round(holiday_amount)
    total_amount = base_amount + overtime_amount + holiday_amount

    # Company breakdown
    breakdown: list[CompanyBreakdown] = []
    if is_calculable and total_hours > _ZERO:
        for cid in sorted(company_hours.keys()):
            comp_hours = company_hours[cid]
            proportion = comp_hours / total_hours
            percent = (proportion * _HUNDRED).quantize(_PERCENT_Q, rounding=ROUND_HALF_EVEN)
            comp_base = _round(base_amount * proportion)
            comp_overtime = _round(overtime_amount * proportion)
            if total_holiday_hours > _ZERO:
                h_prop = company_holiday_hours.get(cid, _ZERO) / total_holiday_hours
                comp_holiday = _round(holiday_amount * h_prop)
            else:
                comp_holiday = _ZERO
            code, name = companies_by_id.get(cid, ("", ""))
            breakdown.append(CompanyBreakdown(
                company_id=cid,
                company_code=code,
                company_name=name,
                hours=comp_hours,
                percent=percent,
                base_amount=comp_base,
                overtime_amount=comp_overtime,
                holiday_amount=comp_holiday,
                total=comp_base + comp_overtime + comp_holiday,
            ))

    return EmployeePayroll(
        employee_id=employee.id,
        employee_name=employee.full_name,
        rate=rate,
        schedule_name=schedule_name,
        total_hours=total_hours,
        norm_hours=norm_hours,
        delta_hours=delta_hours,
        overtime_hours=overtime_hours,
        holiday_hours=total_holiday_hours,
        norm_days=norm_days,
        fact_days=fact_days,
        hourly_rate=hourly_rate,
        base_amount=base_amount,
        overtime_amount=overtime_amount,
        holiday_amount=holiday_amount,
        total_amount=total_amount,
        breakdown_by_company=breakdown,
        is_calculable=is_calculable,
        reason_if_not_calculable=reason,
    )
