from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ApplicationCountInput(BaseModel):
    """Заявки на подбор для юрлица за месяц: в работе и закрытые.

    Общее число не передаётся — оно всегда сумма этих двух (см. модель).
    """

    company_id: int
    in_progress: int = Field(default=0, ge=0)
    closed: int = Field(default=0, ge=0)


class ApplicationShareRead(ApplicationCountInput):
    """Заявки компании + вычисленные общее число и процент распределения."""

    # Всего заявок = в работе + закрытые; это и есть база распределения.
    count: int
    percent: Decimal


class DepartmentApplicationsRead(BaseModel):
    """Заявки отдела за месяц + проценты, по которым делится зарплата отдела.

    Отдаётся только для отделов с флагом «распределение по заявкам»: у остальных
    заявок нет и вводить их негде (task_hr_applications).
    """

    department_id: int
    department_name: str | None = None
    year: int
    month: int
    applications: list[ApplicationShareRead]
    total_in_progress: int
    total_closed: int
    total_applications: int
    # Заявки за месяц не заведены → отдел временно распределяется по обычному
    # каскаду, и это надо показать: молча посчитать «как у всех» нельзя.
    is_empty: bool


class DepartmentApplicationsUpdate(BaseModel):
    """Полный набор заявок отдела за месяц: что прислали, то и будет
    (пустой список или все нули — заявки сняты)."""

    department_id: int
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    applications: list[ApplicationCountInput]


class ApplicationsDistributionRow(BaseModel):
    """Распределение начисленного рабочего места по юрлицам — для показа В ТАБЕЛЕ
    (task_hr_applications).

    Считается на бэке теми же числами, что ведомость: фронт не пересобирает
    «Итого начислено» из кусков расчёта и не может разойтись с /statement.
    """

    employee_id: int
    position_id: int | None = None
    department_id: int | None = None
    accrued_total: Decimal
    # company_id → сумма; сумма значений ровно равна accrued_total
    amounts: dict[int, Decimal]
