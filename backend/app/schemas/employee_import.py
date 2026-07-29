from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.employee import PayType, WeekendPayType


class ImportRowRead(BaseModel):
    """Строка файла после разбора: что распозналось и что с ней не так.

    `raw` — значения как в файле (для показа «как ввели»), остальные поля —
    результат нормализации. Невалидные строки не импортируются.
    """

    row_number: int
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    raw: dict[str, str] = Field(default_factory=dict)

    tab_number: Optional[str] = None
    full_name: Optional[str] = None
    position: Optional[str] = None
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    schedule_id: Optional[int] = None
    schedule_name: Optional[str] = None
    pay_type: PayType = "salary"
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    weekend_pay_type: WeekendPayType = "coefficient"
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    hire_date: Optional[datetime.date] = None

    # Заполняется только при подтверждённом импорте
    created: bool = False
    employee_id: Optional[int] = None


class EmployeeImportResult(BaseModel):
    """Ответ и превью, и подтверждённого импорта — отличаются флагом `confirmed`."""

    confirmed: bool = False
    total: int = 0
    valid_count: int = 0
    error_count: int = 0
    created_count: int = 0
    skipped_count: int = 0
    rows: list[ImportRowRead] = Field(default_factory=list)
