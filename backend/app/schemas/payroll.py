from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel


class CompanyBreakdownRead(BaseModel):
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    percent: Decimal
    overtime_hours: Decimal
    holiday_hours: Decimal
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

    # Отсутствия (ОТ/ДО/Б/Н). *_paid_days — рабочие дни из отмеченных,
    # именно за них считается оплата «оклад/норма × дни × 8».
    vacation_days: int = 0
    unpaid_days: int = 0
    sick_days: int = 0
    absent_days: int = 0
    vacation_paid_days: int = 0
    sick_paid_days: int = 0
    vacation_amount: Decimal = Decimal("0")
    sick_amount: Decimal = Decimal("0")

    # Оплата выходных/праздничных (задача 3.11a п.3 — отображение коэффициента)
    weekend_pay_type: Optional[Literal["coefficient", "fixed_rate"]] = None
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None

    # Премии/KPI/удержания и итог «к выплате» (задача 3.11a п.1,2,4)
    premium_amount: Decimal = Decimal("0")
    kpi_amount: Decimal = Decimal("0")
    advance_deduction: Decimal = Decimal("0")
    loan_deduction: Decimal = Decimal("0")
    loan_remaining: Decimal = Decimal("0")
    loan_planned_deduction: Decimal = Decimal("0")
    loan_is_manual: bool = False
    total_deductions: Decimal = Decimal("0")
    net_payout: Decimal = Decimal("0")

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
    total_vacation_amount: Decimal = Decimal("0")
    total_sick_amount: Decimal = Decimal("0")
    grand_total: Decimal
    total_premium: Decimal = Decimal("0")
    total_kpi: Decimal = Decimal("0")
    total_deductions: Decimal = Decimal("0")
    total_net_payout: Decimal = Decimal("0")
