from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CompanyBreakdownRead(BaseModel):
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    percent: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total: Decimal


class EmployeePayrollRead(BaseModel):
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

    breakdown_by_company: list[CompanyBreakdownRead]
    is_calculable: bool
    reason_if_not_calculable: str | None


class PayrollSummaryRead(BaseModel):
    year: int
    month: int
    employees: list[EmployeePayrollRead]
    total_employees: int
    total_hours: Decimal
    total_base_amount: Decimal
    total_overtime_amount: Decimal
    total_holiday_amount: Decimal
    grand_total: Decimal
