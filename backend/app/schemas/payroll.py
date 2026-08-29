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
    off_schedule_hours: Decimal
    holiday_hours: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    off_schedule_amount: Decimal
    holiday_amount: Decimal
    total: Decimal


class EmployeePayrollRead(BaseModel):
    employee_id: int
    employee_name: str

    # Позиция (рабочее место) строки — task_positions ч.A. У совместителя
    # строк столько же, сколько позиций, employee_id при этом повторяется.
    # «К выплате» между строками одного человека НЕ суммируется.
    position_id: Optional[int] = None
    position_title: Optional[str] = None
    is_primary_position: bool = True

    # rate — оклад окладника; у посменного здесь УСЛОВНЫЙ оклад
    # (ставка × норма смен), от которого считаются отпускные/больничные;
    # у почасовика оклада нет — None.
    rate: Decimal | None
    schedule_name: str | None

    # Тип оплаты позиции: "salary" (оклад), "per_shift" (смены × ставка),
    # "hourly" (часы × ставка за час)
    pay_type: str = "salary"
    shift_rate: Decimal | None = None
    hour_rate: Decimal | None = None
    worked_shifts: int = 0
    norm_shifts: int | None = None
    # Смены в базе посменного (плановые дни графика). Смены в выходной/праздник
    # сюда не входят — они оплачены по коэффициенту (task_shiftpay_addons).
    base_shifts: int = 0

    total_hours: Decimal
    norm_hours: Decimal | None
    delta_hours: Decimal | None
    overtime_hours: Decimal
    # off_schedule_* — выход в свой выходной по графику;
    # holiday_*      — работа в нерабочий праздничный день календаря.
    off_schedule_hours: Decimal
    holiday_hours: Decimal
    norm_days: int | None
    fact_days: int
    hourly_rate: Decimal | None

    base_amount: Decimal
    overtime_amount: Decimal
    off_schedule_amount: Decimal
    holiday_amount: Decimal
    total_amount: Decimal

    # Ночные смены (task_night_shifts_rework): число отмеченных смен, ставка
    # (фонд отдела / календарные дни месяца) и надбавка = смены × ставка.
    # Надбавка входит в total_amount и дальше в «к выплате».
    night_shifts: int = 0
    night_rate: Decimal | None = None
    night_amount: Decimal = Decimal("0")

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

    # Годовой лимит больничного (часть 2): израсходовано до месяца, сверх лимита
    # в этом месяце и остаток на конец месяца.
    sick_limit_days: int = 0
    sick_days_used_before: int = 0
    sick_unpaid_days: int = 0
    sick_limit_remaining: int = 0

    # Оплата часов вне графика (задача 3.11a п.3 — отображение коэффициента)
    weekend_pay_type: Optional[Literal["coefficient", "fixed_rate"]] = None
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None

    # Оплата праздничных часов — отдельная настройка, дефолт коэффициента 2.0
    holiday_pay_type: Optional[Literal["coefficient", "fixed_rate"]] = None
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None

    # Премии/KPI/удержания и итог «к выплате» (задача 3.11a п.1,2,4)
    premium_amount: Decimal = Decimal("0")
    kpi_amount: Decimal = Decimal("0")
    advance_deduction: Decimal = Decimal("0")
    loan_deduction: Decimal = Decimal("0")
    loan_remaining: Decimal = Decimal("0")
    loan_planned_deduction: Decimal = Decimal("0")
    loan_is_manual: bool = False
    total_deductions: Decimal = Decimal("0")
    # net_payout округлён математически до 1000 ₽ (task_payout_rounding);
    # exact/tail — справочно, знак tail любой
    net_payout: Decimal = Decimal("0")
    net_payout_exact: Decimal = Decimal("0")
    rounding_tail: Decimal = Decimal("0")

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
    total_off_schedule_amount: Decimal = Decimal("0")
    total_holiday_amount: Decimal
    total_vacation_amount: Decimal = Decimal("0")
    total_sick_amount: Decimal = Decimal("0")
    total_night_amount: Decimal = Decimal("0")
    grand_total: Decimal
    total_premium: Decimal = Decimal("0")
    total_kpi: Decimal = Decimal("0")
    total_deductions: Decimal = Decimal("0")
    # Сумма ОКРУГЛЁННЫХ выплат (не округление суммы) + справочные точная и хвосты
    total_net_payout: Decimal = Decimal("0")
    total_net_payout_exact: Decimal = Decimal("0")
    total_rounding_tail: Decimal = Decimal("0")
