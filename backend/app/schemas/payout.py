from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

AdjustmentKindType = Literal["premium", "kpi", "advance"]


class AdjustmentCreate(BaseModel):
    employee_id: int
    # Рабочее место, к которому относится премия/KPI/аванс (task_positions ч.A):
    # деньги попадут в «к выплате» именно этой позиции. Не указано — основная.
    position_id: Optional[int] = None
    year: int
    month: int
    kind: AdjustmentKindType
    amount: Decimal
    reason: str
    # Источник финансирования (task_funding_source): юрлицо, оплачивающее эту
    # премию/KPI. Необязателен — без него распределение идёт общим каскадом.
    # У аванса источника нет (это удержание) — роутер отвергает такую попытку.
    funding_company_id: Optional[int] = None

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        return v

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, v: str) -> str:
        if not v or len(v.strip()) < 3:
            raise ValueError("Обоснование обязательно (минимум 3 символа)")
        return v.strip()

    @field_validator("month")
    @classmethod
    def _month_range(cls, v: int) -> int:
        if not (1 <= v <= 12):
            raise ValueError("Неверный месяц")
        return v


class AdjustmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    position_id: Optional[int] = None
    year: int
    month: int
    kind: AdjustmentKindType
    amount: Decimal
    reason: str
    # Источник финансирования (task_funding_source); None — обычный каскад.
    # Имя юрлица идёт рядом, чтобы список премий подписывался без справочника.
    funding_company_id: Optional[int] = None
    funding_company_name: Optional[str] = None
    created_by_id: Optional[int] = None
    created_at: Optional[str] = None


class LoanOverrideInput(BaseModel):
    employee_id: int
    year: int
    month: int
    actual_amount: Decimal

    @field_validator("actual_amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Сумма удержания не может быть отрицательной")
        return v


class LoanInfo(BaseModel):
    """Сводка по займу сотрудника на конкретный месяц."""
    loan_amount: Optional[Decimal] = None
    loan_term_months: Optional[int] = None
    loan_start_date: Optional[date] = None
    planned_deduction: Decimal = Decimal("0")
    actual_deduction: Decimal = Decimal("0")
    remaining_before: Decimal = Decimal("0")
    remaining_after: Decimal = Decimal("0")
    is_manual: bool = False
    is_active: bool = False
