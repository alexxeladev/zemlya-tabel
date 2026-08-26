from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ApplicationCountInput(BaseModel):
    """Число заявок на подбор, отработанных для юрлица за месяц."""

    company_id: int
    count: int = Field(ge=0)


class ApplicationShareRead(ApplicationCountInput):
    """Заявки компании вместе с вычисленным из них процентом распределения."""

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
