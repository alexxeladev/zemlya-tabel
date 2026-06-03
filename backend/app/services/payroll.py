from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.calendar import is_holiday, norm_hours_for_period

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_ONE_HALF = Decimal("1.5")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_ONE, rounding=ROUND_HALF_EVEN)


@dataclass
class CompanyBreakdown:
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
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

    # Aggregate hours by company; detect holiday hours per company
    company_hours: dict[int, Decimal] = {}
    company_holiday_hours: dict[int, Decimal] = {}
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

        if calendar_data is not None and is_holiday(
            calendar_data, entry.work_date.month, entry.work_date.day
        ):
            company_holiday_hours[cid] += h
            total_holiday_hours += h

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

    # Hours stats (always computed when norm is available)
    delta_hours: Decimal | None = None
    overtime_hours = _ZERO
    if norm_hours is not None:
        delta_hours = total_hours - norm_hours
        overtime_hours = max(_ZERO, delta_hours)

    # Financial amounts
    hourly_rate: Decimal | None = None
    base_amount = _ZERO
    overtime_amount = _ZERO
    holiday_amount = _ZERO

    if is_calculable and rate is not None and norm_hours is not None and norm_hours > _ZERO:
        hourly_rate = rate / norm_hours
        base_amount = rate if total_hours >= norm_hours else rate * total_hours / norm_hours
        overtime_amount = overtime_hours * hourly_rate * _ONE_HALF
        holiday_amount = total_holiday_hours * hourly_rate * _HALF

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
        hourly_rate=hourly_rate,
        base_amount=base_amount,
        overtime_amount=overtime_amount,
        holiday_amount=holiday_amount,
        total_amount=total_amount,
        breakdown_by_company=breakdown,
        is_calculable=is_calculable,
        reason_if_not_calculable=reason,
    )
