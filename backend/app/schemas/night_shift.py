from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class NightShiftRead(BaseModel):
    """Отметка выхода в ночь: сотрудник, рабочее место, дата. Часов нет —
    оплачивается сам факт смены (task_night_shifts_rework)."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    position_id: int
    work_date: date


class NightShiftInput(BaseModel):
    """Отметить (value=true) или снять (false) ночную смену."""

    employee_id: int
    position_id: Optional[int] = None
    work_date: date
    value: bool = True


class NightFundRead(BaseModel):
    """Состояние фонда ночных смен отдела за месяц — индикатор остатка.

    Суммы (`fund`, `rate`) — деньги: табельщику они не отдаются, а смены
    (`limit`, `used`, `remaining`) видны всем, кто ведёт табель.
    """

    department_id: int
    department_name: Optional[str] = None
    fund: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    limit_shifts: int
    used_shifts: int
    remaining_shifts: int
