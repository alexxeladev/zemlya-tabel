from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CompanyShareInput(BaseModel):
    company_id: int
    percent: Decimal


class EmployeeSharesRead(BaseModel):
    """Проценты распределения по умолчанию из карточки сотрудника.

    Дополнительно отдаётся дефолт отдела — чтобы в карточке было видно, что
    при пустом собственном распределении наследуется отдел (ч.3 каскада).
    """
    employee_id: int
    shares: list[CompanyShareInput]
    percent_sum: Decimal
    department_id: int | None = None
    department_name: str | None = None
    department_shares: list[CompanyShareInput] = []
    # Своё распределение не задано, а у отдела есть → сотрудник наследует отдел
    inherits_department: bool = False


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

    # rate — оклад окладника; у посменного УСЛОВНЫЙ оклад (ставка × норма смен).
    # Что перед нами, видно по pay_type.
    rate: Decimal | None
    pay_type: str = "salary"
    shift_rate: Decimal | None = None
    worked_shifts: int = 0
    norm_shifts: int | None = None
    norm_hours: Decimal | None
    fact_hours: Decimal
    overtime_coefficient: Decimal
    overtime_hours: Decimal
    overtime_amount: Decimal

    # Начислено оклад (включает оплату часов вне графика и праздничных)
    base_salary: Decimal
    premium_amount: Decimal       # Премия базовая
    kpi_amount: Decimal           # KPI
    premium_extra_amount: Decimal  # Премия (доп.) — пока не моделируется, плейсхолдер

    # Отсутствия: дни и оплата (ДО/Н — только дни, денег не дают)
    vacation_days: int = 0
    sick_days: int = 0
    unpaid_days: int = 0
    absent_days: int = 0
    vacation_amount: Decimal = Decimal("0")
    sick_amount: Decimal = Decimal("0")
    # Годовой лимит больничного (часть 2)
    sick_limit_days: int = 0
    sick_unpaid_days: int = 0
    sick_limit_remaining: int = 0

    # Итого начислено = оклад + переработка + отпускные + больничные + премии + KPI
    accrued_total: Decimal
    deductions: Decimal           # Аванс/Удержано (займ + аванс)
    net_payout: Decimal           # К выплате — округлено вниз до 100 ₽
    net_payout_exact: Decimal = Decimal("0")  # до округления (справочно)
    rounding_tail: Decimal = Decimal("0")     # хвост = точное − округлённое

    is_overridden: bool           # проценты распределения переопределены на месяц
    is_auto_distributed: bool     # распределено авто по фактическим часам (ручной % не задан)
    # Уровень каскада, откуда взято распределение:
    # month (правка на месяц) > employee (карточка) > department (отдел) > hours (авто)
    distribution_source: str
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
    total_vacation_amount: Decimal = Decimal("0")
    total_sick_amount: Decimal = Decimal("0")
    total_premium: Decimal
    total_kpi: Decimal
    total_accrued: Decimal
    total_deductions: Decimal
    total_net_payout: Decimal  # Σ округлённых выплат (не округление суммы)
    total_net_payout_exact: Decimal = Decimal("0")
    total_rounding_tail: Decimal = Decimal("0")
    # Итог распределения по каждой компании: {company_id: amount}
    distribution_totals: dict[int, Decimal]
