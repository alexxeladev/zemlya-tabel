from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CompanyShareInput(BaseModel):
    company_id: int
    percent: Decimal


class EmployeeSharesRead(BaseModel):
    """Проценты распределения по умолчанию из карточки сотрудника."""
    employee_id: int
    shares: list[CompanyShareInput]
    percent_sum: Decimal


class EmployeeSharesUpdate(BaseModel):
    shares: list[CompanyShareInput]


class DistributionOverrideInput(BaseModel):
    """Переопределение распределения на конкретный месяц (правка в ведомости)."""
    employee_id: int
    year: int
    month: int
    shares: list[CompanyShareInput]


class StatementCompanyRef(BaseModel):
    id: int
    code: str
    name: str


class StatementCompanyAmount(BaseModel):
    company_id: int
    percent: Decimal
    amount: Decimal


class StatementRow(BaseModel):
    employee_id: int
    tab_number: str | None
    employee_name: str
    main_company_id: int | None
    main_company_name: str | None
    department_name: str | None
    position: str | None
    schedule_name: str | None

    rate: Decimal | None
    norm_hours: Decimal | None
    fact_hours: Decimal
    overtime_coefficient: Decimal
    overtime_hours: Decimal
    overtime_amount: Decimal

    # Начислено оклад (включает оплату праздничных/выходных часов)
    base_salary: Decimal
    premium_amount: Decimal       # Премия базовая
    kpi_amount: Decimal           # KPI
    premium_extra_amount: Decimal  # Премия (доп.) — пока не моделируется, плейсхолдер

    accrued_total: Decimal        # Итого начислено = оклад + переработка + премии + KPI
    deductions: Decimal           # Аванс/Удержано (займ + аванс)
    net_payout: Decimal           # К выплате

    is_overridden: bool           # проценты распределения переопределены на месяц
    is_auto_distributed: bool     # распределено авто по фактическим часам (ручной % не задан)
    percent_sum: Decimal          # сумма процентов (для подсветки ≠ 100)
    distribution: list[StatementCompanyAmount]
    distribution_total: Decimal

    is_calculable: bool
    note: str | None


class PayrollStatementRead(BaseModel):
    year: int
    month: int
    companies: list[StatementCompanyRef]
    rows: list[StatementRow]

    total_overtime_amount: Decimal
    total_base_salary: Decimal
    total_premium: Decimal
    total_kpi: Decimal
    total_accrued: Decimal
    total_deductions: Decimal
    total_net_payout: Decimal
    # Итог распределения по каждой компании: {company_id: amount}
    distribution_totals: dict[int, Decimal]
