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
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    # Даты начала изменения условий здесь НЕТ (ADR-001): условия меняются своей
    # точкой входа явным списком изменений (`TermsChangesInput`), а общая правка
    # их не принимает вовсе — иначе правка снова уходила бы не в тот месяц.


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


TermGroup = Literal["pay", "schedule", "weekend", "holiday", "overtime", "official"]


class TermChangeRead(BaseModel):
    """Одно изменение одного условия — строка истории (ADR-001).

    `value` — в том же виде, в каком его принимает диалог правки: деньги и
    коэффициенты строкой, график идентификатором (название — в
    `PositionTermsStateRead.schedule_names`), признак — булевым.
    """

    field: str
    field_label: str
    group: TermGroup
    group_label: str
    #: None — базовое значение «с начала», а не изменение.
    effective_from: Optional[datetime.date] = None
    effective_label: str
    value: Any = None
    created_by_name: Optional[str] = None
    created_at: Optional[datetime.datetime] = None


class TermPlannedRead(BaseModel):
    """Изменение группы, которое ещё НЕ действует: «с 01.12.2026 будет …»."""

    effective_from: datetime.date
    effective_label: str
    fields: list[str]
    values: dict[str, Any]


class TermGroupStateRead(BaseModel):
    """Группа условий в карточке: значения сегодня и что запланировано дальше."""

    group: TermGroup
    label: str
    fields: list[str]
    current: dict[str, Any]
    planned: list[TermPlannedRead] = []


class PositionTermsStateRead(BaseModel):
    """Условия рабочего места для карточки: сегодня, запланированное, история."""

    position_id: int
    today: datetime.date
    #: Дата, которую диалог подставит по умолчанию (1-е число следующего месяца).
    default_effective_from: datetime.date
    #: «С начала времён» — так помечены базовые значения, заведённые при найме.
    beginning: datetime.date
    groups: list[TermGroupStateRead]
    changes: list[TermChangeRead] = []
    #: id графика → название (в том числе снятого с учёта).
    schedule_names: dict[str, str] = {}


class TermsChangeInput(BaseModel):
    """Одно изменение ГРУППЫ условий с датой начала действия.

    Присылается ровно то, что изменил человек: группа, её значения и дата.
    Бэкенд ничего не диффит — значение, совпадающее с действующим, тоже
    записывается (дату выбрал человек осознанно).
    """

    group: TermGroup
    effective_from: datetime.date
    pay_type: Optional[PayType] = None
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    schedule_id: Optional[int] = None
    weekend_pay_type: Optional[WeekendPayType] = None
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    holiday_pay_type: Optional[WeekendPayType] = None
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    is_official: Optional[bool] = None
    official_salary: Optional[Decimal] = None


class TermsChangesInput(BaseModel):
    """Список изменений: разные группы можно менять разными датами в один заход."""

    changes: list[TermsChangeInput] = Field(min_length=1)
