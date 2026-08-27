from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from app.schemas.timesheet_period import PeriodStatus


class HoursSummaryRead(BaseModel):
    """KPI-карточки блока «Часы»."""
    total_hours: Decimal
    norm_hours: Decimal | None
    overtime_hours: Decimal
    percent_of_norm: Decimal | None  # total/norm × 100, одна десятая


class DepartmentHoursRead(BaseModel):
    department_id: int | None
    department_name: str
    total_hours: Decimal
    norm_hours: Decimal | None
    overtime_hours: Decimal


class PayrollTotalsRead(BaseModel):
    """KPI-карточки блока «ФОТ» (брутто к начислению)."""
    total: Decimal
    base: Decimal
    overtime: Decimal
    off_schedule: Decimal = Decimal("0")
    holiday: Decimal
    # Эффект округления «к выплате» вниз до 100 ₽ — сумма хвостов всех
    # сотрудников за период (осело в пользу компании), task_payout_rounding
    rounding_effect: Decimal = Decimal("0")
    # Сотрудники, не вошедшие в расчёт (нет оклада/графика/сменный график):
    # ФОТ на дашборде по ним занижен — фронт показывает предупреждение.
    non_calculable_employees: int


class DepartmentPayrollRead(BaseModel):
    department_id: int | None
    department_name: str
    total: Decimal


class CompanyPayrollRead(BaseModel):
    company_id: int
    company_code: str
    company_name: str
    # Короткое имя для подписи на графике (ч.2); полное остаётся в company_name.
    company_display_name: str = ""
    total: Decimal


class PeriodCountsRead(BaseModel):
    closed: int
    pending_review: int
    draft: int
    overdue: int


class PeriodStatusRowRead(BaseModel):
    period_id: int | None  # None — период ещё не создан (lazy) → считается draft
    department_id: int | None
    department_name: str
    year: int
    month: int
    status: PeriodStatus
    submitted_by_name: str | None
    closed_by_name: str | None
    is_overdue: bool


class PeriodsBlockRead(BaseModel):
    counts: PeriodCountsRead
    rows: list[PeriodStatusRowRead]          # выбранный месяц
    overdue_rows: list[PeriodStatusRowRead]  # незакрытые периоды прошлых месяцев


class TrendPointRead(BaseModel):
    year: int
    month: int
    total_hours: Decimal
    overtime_hours: Decimal
    payroll_total: Decimal | None  # None для employee (финансы скрыты)


class DashboardResponse(BaseModel):
    # (year, month) — НАЧАЛО периода, (to_year, to_month) — конец включительно.
    # Для одиночного месяца конец совпадает с началом, а months_count == 1 —
    # старые клиенты, читающие только year/month, ничего не замечают.
    year: int
    month: int
    to_year: int
    to_month: int
    months_count: int = 1
    role: str

    hours: HoursSummaryRead
    hours_by_department: list[DepartmentHoursRead]

    payroll: PayrollTotalsRead | None  # None для employee
    payroll_by_department: list[DepartmentPayrollRead]
    payroll_by_company: list[CompanyPayrollRead]

    periods: PeriodsBlockRead | None  # None для employee

    trend: list[TrendPointRead]
