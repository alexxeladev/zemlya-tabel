"""
Схемы позиции сотрудника — «рабочего места» (task_positions ч.B, UI).

Часть A завела модель `EmployeePosition` и расчёт по каждой позиции; здесь
появляется её представление в API: карточка сотрудника управляет списком
должностей, табель строит по строке на позицию.

Оклад / ставка за смену / ставка за час — взаимоисключающие: при смене типа
оплаты поля чужих типов гасятся (`PAY_TYPE_BASE_FIELD`), иначе расчёт молча
возьмёт не ту базу.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.company import CompanyRead
from app.schemas.department import DepartmentRead
from app.schemas.schedule import ScheduleRead

PayType = Literal["salary", "per_shift", "hourly"]
WeekendPayType = Literal["coefficient", "fixed_rate"]


class EmployeePositionBase(BaseModel):
    title: Optional[str] = None
    department_id: Optional[int] = None
    schedule_id: Optional[int] = None
    company_id: Optional[int] = None
    pay_type: PayType = "salary"
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    weekend_pay_type: WeekendPayType = "coefficient"
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    holiday_pay_type: WeekendPayType = "coefficient"
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    # Ставка ночной смены на позиции НЕ задаётся: она вычисляется из фонда
    # отдела (task_night_shifts_rework), здесь остался только флаг.
    has_night_shifts: bool = False
    # Период работы НА ЭТОЙ должности (task_employment_period): границы
    # заполнения табеля, включительно. Пустая дата — границы нет. Действуют в
    # пересечении с датами человека.
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None
    is_active: bool = True
    sort_order: int = 0


class EmployeePositionCreate(EmployeePositionBase):
    # Новая позиция может сразу стать основной — прежняя основная тогда
    # становится совместительством (основная всегда ровно одна).
    is_primary: bool = False


class EmployeePositionUpdate(BaseModel):
    title: Optional[str] = None
    department_id: Optional[int] = None
    schedule_id: Optional[int] = None
    company_id: Optional[int] = None
    pay_type: Optional[PayType] = None
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    weekend_pay_type: Optional[WeekendPayType] = None
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    holiday_pay_type: Optional[WeekendPayType] = None
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    has_night_shifts: Optional[bool] = None
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    # С какой даты действует изменение условий (ставка, тип оплаты, график,
    # коэффициенты) — task_stage3_historicity. Не задано — 1-е число
    # следующего месяца. На поля, которые не версионируются, не влияет.
    terms_effective_from: Optional[datetime.date] = None


class EmployeePositionRead(EmployeePositionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    is_primary: bool
    # Подпись для UI: должность, а без неё — «Основная»/«Совместительство»
    display_title: str

    department: Optional[DepartmentRead] = None
    schedule: Optional[ScheduleRead] = None
    company: Optional[CompanyRead] = None


class PositionTermsRead(BaseModel):
    """Версия условий рабочего места для истории в карточке
    (task_stage3_historicity): что действовало и с какой даты."""

    id: int
    #: None — с начала (первая версия, перенесённая миграцией).
    effective_from: Optional[datetime.date] = None
    pay_type: str
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    schedule_id: Optional[int] = None
    schedule_name: Optional[str] = None
    weekend_pay_type: str
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    holiday_pay_type: str
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    is_official: bool = False
    official_salary: Optional[Decimal] = None
    #: Подписи полей, изменившихся относительно предыдущей версии.
    changed: list[str] = []
    created_by_name: Optional[str] = None
    created_at: Optional[datetime.datetime] = None


class PositionTermsHistoryRead(BaseModel):
    position_id: int
    versions: list[PositionTermsRead]
    #: Дата, которую форма подставит по умолчанию (1-е число следующего месяца).
    default_effective_from: datetime.date
